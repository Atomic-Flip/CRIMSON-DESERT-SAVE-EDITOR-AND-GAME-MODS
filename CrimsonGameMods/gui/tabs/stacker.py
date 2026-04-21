"""
Stacker Tool — multi-mod iteminfo.pabgb merger.

Drop N mods (compiled PAZ folder / loose pabgb / legacy JMM format:2 JSON)
into the window, click Install Stack, get ONE merged overlay in the game.

Field-level merge, not byte-level:
- Two mods touching different fields of the same entry both land.
- Two mods touching the SAME field with different values → shown in
  conflict table, user picks or last-writer wins.
- Mods that add entries just extend the merged dict list; no offset math.

Everything runs on primitives already in the codebase:
- crimson_rs.parse_iteminfo_from_bytes  (→ dict items)
- paz_patcher.ItemBuffPatcher.find_items (→ entry→BlobStart for legacy JSON resolve)
- crimson_rs.serialize_iteminfo (→ merged bytes)
- buffs_v319._buff_export_cdumm_mod / _buff_apply_to_game (→ overlay write + install)

No byte-level truncation. No MergePabgb pad/truncate. Output goes
through crimson_rs.serialize_iteminfo which rebuilds sizes from the
dicts, so Super MOD's entries keep their full shape.
"""
from __future__ import annotations
import os, sys, json, struct, copy, zipfile, tempfile, shutil, logging, time
from dataclasses import dataclass, field  # field: default_factory for mutable defaults
from pathlib import Path
from typing import Optional

from . import iteminfo_inspector
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFontMetrics, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QTextEdit, QLineEdit, QFileDialog,
    QMessageBox, QGroupBox, QHeaderView, QAbstractItemView, QSplitter,
    QApplication, QSizePolicy, QFrame, QListWidget, QListWidgetItem,
    QCheckBox, QToolButton,
)


def _size_button(btn: QPushButton, extra_px: int = 28, primary: bool = False) -> QPushButton:
    """Size a button so its label never clips, regardless of system font.

    Measures the text width at the widget's current font, adds padding for
    the border + internal margin, and fixes that as minimumWidth. Keeps a
    fixed vertical size policy (buttons never stretch tall) and an
    expanding-or-preferred horizontal policy so rows of buttons share
    width evenly in tight layouts.
    """
    fm = QFontMetrics(btn.font())
    w = fm.horizontalAdvance(btn.text()) + extra_px
    btn.setMinimumWidth(w)
    btn.setMinimumHeight(30 if not primary else 34)
    btn.setSizePolicy(
        QSizePolicy.Expanding if primary else QSizePolicy.Preferred,
        QSizePolicy.Fixed,
    )
    return btn

log = logging.getLogger(__name__)

INTERNAL_DIR = "gamedata/binary__/client/bin"
ITEMINFO_PABGB = "iteminfo.pabgb"
ITEMINFO_PABGH = "iteminfo.pabgh"

# Non-numeric group name, deliberate. JMM's CleanupStaleOverlayGroups
# (see _ilspy_dump_mod_manager/source_v10/CDModManager/ModManager.cs:2485)
# deletes any overlay directory whose name passes `All(char.IsDigit)` and
# is >= 36, unless it's in JMM's own papgt backup. A non-numeric name
# like "stk1" is invisible to that predicate — our overlay survives
# subsequent JMM Apply runs. The game's PAPGT loader does not require
# numeric group names; that convention is JMM's alone.
# Apply Stack uses numeric group names because the game's PAPGT loader
# silently rejects non-numeric group names like "stk1" -- the PAZ never
# loads even though PAPGT registration looks correct on disk.
# Confirmed empirically 2026-04-21.
#
# Tradeoff: JMM's CleanupStaleOverlayGroups pass wipes all-numeric group
# dirs it doesn't recognize. Users who run JMM after Apply Stack must
# re-apply. Non-numeric would have survived JMM but the game doesn't
# load them -- functionality beats compatibility.
OVERLAY_GROUP_DEFAULT = "0062"
# Equipslotinfo MUST ship in a separate overlay group from iteminfo.
# Bundling them together in one group empirically breaks UP v2
# (guns-on-Kliff etc.) -- confirmed 2026-04-21 against v1.0.3 split
# deployment that works.
OVERLAY_EQUIP_GROUP_DEFAULT = "0063"


# ============================================================
#   Mod classification / ingestion
# ============================================================

@dataclass
class ModEntry:
    """A single source in the stack. Four kinds, four state buckets.

    Bucket A (dict-level edits) → parsed_items
    Bucket B (checkbox toggles) → apply_stacks, apply_inf_dura
    Bucket C (post-serialize byte patches) → cd_patches, transmog_swaps, vfx_*
    Bucket D (sibling files bundled in same overlay) → staged_skill_files,
        staged_equip_files

    External mod sources (folder_paz / loose_pabgb / legacy_json) can
    populate A and D. ItemBuffs pulls populate all four buckets since
    ItemBuffs applies all four when its own Apply-to-Game button runs
    — see buffs_v319.py:9770.
    """
    name: str
    path: str
    kind: str                # "folder_paz" | "loose_pabgb" | "legacy_json" | "itembuffs_edits"
    group: str = ""          # folder_paz only: "0036" etc.
    ok: bool = True
    note: str = ""
    enabled: bool = True     # user toggle — disabled sources skip the merge
    effective_pabgb: Optional[bytes] = None   # filled during install prep (external mods only)
    parsed_items: Optional[list] = None       # Bucket A — dict list
    apply_stats: str = ""    # "411 applied, 0 skipped" etc.
    detected_features: dict = field(default_factory=dict)   # feature → count
    # Bucket B — checkbox toggles. None/False means "don't apply".
    apply_stacks: Optional[int] = None        # target stack value, e.g. 9999
    apply_inf_dura: bool = False
    # Bucket C — post-serialize byte patches. All lists/dicts captured
    # by deep-copy at Pull time so subsequent ItemBuffs edits don't
    # mutate what we captured.
    cd_patches: dict = field(default_factory=dict)
    transmog_swaps: list = field(default_factory=list)
    vfx_size_changes: list = field(default_factory=list)
    vfx_swaps: list = field(default_factory=list)
    vfx_anim_swaps: list = field(default_factory=list)
    vfx_attach_changes: list = field(default_factory=list)
    # Bucket D — sibling files to add alongside iteminfo in the overlay PAZ.
    # Keys are "skill.pabgb", "skill.pabgh", "equipslotinfo.pabgb",
    # "equipslotinfo.pabgh". Values are raw bytes.
    staged_skill_files: dict = field(default_factory=dict)
    staged_equip_files: dict = field(default_factory=dict)
    # Legacy-JSON-only: per-patch field attribution + merge strategy. See
    # iteminfo_inspector.py. `merge_mode` is "strict" (default — apply
    # bytes to vanilla then re-parse) or "semantic" (skip byte apply,
    # convert each patch to a field-level edit that survives offset
    # drift from other mods). `inspections` is populated during
    # _run_inner; the Details pane reads it to show per-patch field
    # names and any stale/missing reasons.
    merge_mode: str = "strict"
    inspections: list = field(default_factory=list)
    # Populated when merge_mode == "reparse_diff". A
    # iteminfo_inspector.ReparseDiffReport or None.
    reparse_report: object = None


# Kliff's 11 tribe_gender hashes — if ALL of them are present in an
# item's prefab tribe_gender_list, it's an unambiguous signal that
# Universal Proficiency v2 was applied (vanilla gear never has them
# unioned in). Kept in sync with buffs_v319.py:_CHAR_TRIBE_HASHES[1].
_UP_V2_KLIFF_HASHES = frozenset({
    0x13FB2B6E, 0x26BE971F, 0x87D08287, 0x8BF46446,
    0xABFCD791, 0xBFA1F64B, 0xD0A2E1EF, 0xF96C1DD4,
    0xFC66D914, 0xFE7169E2, 0xFF16A579,
})


def _detect_dict_features(items: list) -> dict:
    """Scan parsed iteminfo dict list, count ItemBuffs feature signatures.

    Used at Pull time so the user can *see* that UP v2, Make Dyeable,
    Socket Extend, No Cooldown, etc. were actually captured — not just
    "6024 entries" with the real content hidden inside. Also flags
    missing-equipslotinfo when tribe edits are detected so we can run an
    auto-stage fallback.

    Detection uses only unambiguous field values (e.g. `max_endurance ==
    65535` = Inf Dura, not a vanilla coincidence). Ambiguous signals
    (modified enchant stats, added buffs) are skipped — they need a
    vanilla baseline to detect correctly.
    """
    feats = {
        'max_stacks': 0, 'max_stacks_target': 0,
        'no_cooldown': 0,
        'inf_durability': 0,
        'max_charges': 0,
        'dyeable_flipped': 0,
        'socket_forced_on': 0,
        'socket_5_extended': 0,
        'up_v2_tribe_unioned': 0,
        'passives_added': 0,
        'extra_enchant_levels': 0,
    }
    for it in items:
        stack = it.get('max_stack_count') or 0
        if stack >= 1000:
            feats['max_stacks'] += 1
            if stack > feats['max_stacks_target']:
                feats['max_stacks_target'] = stack

        if it.get('cooltime') == 1:
            feats['no_cooldown'] += 1

        if it.get('max_endurance') == 65535:
            feats['inf_durability'] += 1

        if (it.get('max_charged_useable_count') or 0) >= 99:
            feats['max_charges'] += 1

        if it.get('equip_type_info') and it.get('is_dyeable') == 1:
            feats['dyeable_flipped'] += 1

        ddd = it.get('drop_default_data') or {}
        if ddd.get('use_socket') == 1:
            sock_list = ddd.get('add_socket_material_item_list') or []
            if len(sock_list) >= 5:
                feats['socket_5_extended'] += 1
            # "Forced on" heuristic: accessories/cloaks vanilla ship with
            # use_socket=0; if it's 1 AND has exactly 5 entries at the
            # DennyBro default cost pattern (500, 1000, 2000, 3000, 4000)
            # → our force-enable likely did this.
            if len(sock_list) == 5:
                costs = [e.get('value') for e in sock_list]
                if costs == [500, 1000, 2000, 3000, 4000]:
                    feats['socket_forced_on'] += 1

        if it.get('equip_passive_skill_list'):
            feats['passives_added'] += 1

        for pd in (it.get('prefab_data_list') or []):
            tg = pd.get('tribe_gender_list') or []
            if tg and _UP_V2_KLIFF_HASHES.issubset(set(tg)):
                feats['up_v2_tribe_unioned'] += 1
                break

        enchants = it.get('enchant_data_list') or []
        if len(enchants) >= 12:
            feats['extra_enchant_levels'] += 1

    return feats


def _features_summary_lines(feats: dict) -> list:
    """Render detected features as human-readable lines. Empty list
    means no unambiguous feature fingerprints were found."""
    lines = []
    if feats.get('max_stacks'):
        lines.append(f"  Max Stacks            : {feats['max_stacks']:>5} items "
                     f"(up to {feats['max_stacks_target']})")
    if feats.get('no_cooldown'):
        lines.append(f"  No Cooldown (=1)      : {feats['no_cooldown']:>5} items")
    if feats.get('inf_durability'):
        lines.append(f"  Inf Durability        : {feats['inf_durability']:>5} items")
    if feats.get('max_charges'):
        lines.append(f"  Max Charges (=99)     : {feats['max_charges']:>5} items")
    if feats.get('dyeable_flipped'):
        lines.append(f"  Made Dyeable          : {feats['dyeable_flipped']:>5} items")
    if feats.get('socket_5_extended'):
        lines.append(f"  Sockets (5 slots)     : {feats['socket_5_extended']:>5} items")
    if feats.get('socket_forced_on'):
        lines.append(f"    └ force-enabled     : {feats['socket_forced_on']:>5} accessories/cloaks (DennyBro pattern)")
    if feats.get('up_v2_tribe_unioned'):
        lines.append(f"  UP v2 tribe unioned   : {feats['up_v2_tribe_unioned']:>5} items")
    if feats.get('passives_added'):
        lines.append(f"  Passives attached     : {feats['passives_added']:>5} items")
    if feats.get('extra_enchant_levels'):
        lines.append(f"  Extended enchant sets : {feats['extra_enchant_levels']:>5} items (≥12 levels)")
    return lines


def _compact_features_summary(feats: dict) -> str:
    """One-line summary for the sources-table note column."""
    bits = []
    if feats.get('max_stacks'):
        bits.append(f"stacks({feats['max_stacks']})")
    if feats.get('no_cooldown'):
        bits.append(f"no-cd({feats['no_cooldown']})")
    if feats.get('inf_durability'):
        bits.append(f"inf-dura({feats['inf_durability']})")
    if feats.get('max_charges'):
        bits.append(f"max-charge({feats['max_charges']})")
    if feats.get('dyeable_flipped'):
        bits.append(f"dyeable({feats['dyeable_flipped']})")
    if feats.get('socket_5_extended'):
        bits.append(f"sock-5({feats['socket_5_extended']})")
    if feats.get('up_v2_tribe_unioned'):
        bits.append(f"UP-v2({feats['up_v2_tribe_unioned']})")
    if feats.get('passives_added'):
        bits.append(f"passives({feats['passives_added']})")
    return "; ".join(bits)


def _find_loose_iteminfo(path: str) -> Optional[str]:
    if os.path.isfile(path) and os.path.basename(path).lower() == ITEMINFO_PABGB:
        return path
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            if ITEMINFO_PABGB in files:
                return os.path.join(root, ITEMINFO_PABGB)
    return None


def _find_folder_paz_group(path: str) -> Optional[str]:
    """Return group name (e.g. '0036') if the folder has NNNN/0.paz AND
    iteminfo.pabgb is inside that PAZ. Else None."""
    if not os.path.isdir(path):
        return None
    try:
        import crimson_rs
    except Exception:
        return None
    for name in sorted(os.listdir(path)):
        sub = os.path.join(path, name)
        if not (os.path.isdir(sub) and len(name) == 4 and name.isdigit()
                and os.path.isfile(os.path.join(sub, "0.paz"))
                and os.path.isfile(os.path.join(sub, "0.pamt"))):
            continue
        try:
            crimson_rs.extract_file(path, name, INTERNAL_DIR, ITEMINFO_PABGB)
            return name
        except Exception:
            continue
    return None


def _classify(path: str) -> ModEntry:
    display = Path(path).stem or path
    pabgb = _find_loose_iteminfo(path)
    if pabgb:
        return ModEntry(name=display, path=pabgb, kind="loose_pabgb",
                        note="loose iteminfo.pabgb")
    grp = _find_folder_paz_group(path)
    if grp:
        return ModEntry(name=display, path=path, kind="folder_paz",
                        group=grp, note=f"compiled PAZ mod (group {grp})")
    if os.path.isfile(path) and path.lower().endswith(".json"):
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
            patches = doc.get("patches") or []
            if any("iteminfo.pabgb" in (pt.get("game_file") or "").lower()
                   for pt in patches):
                n = sum(len(pt.get("changes") or []) for pt in patches)
                return ModEntry(name=display, path=path, kind="legacy_json",
                                note=f"legacy JMM JSON ({n} byte patches)")
            return ModEntry(name=display, path=path, kind="",
                            ok=False, note="JSON does not target iteminfo.pabgb")
        except Exception as e:
            return ModEntry(name=display, path=path, kind="",
                            ok=False, note=f"JSON parse failed: {e}")
    if os.path.isdir(path):
        return ModEntry(name=display, path=path, kind="", ok=False,
                        note="folder has no iteminfo.pabgb (not an iteminfo mod)")
    return ModEntry(name=display, path=path, kind="", ok=False,
                    note="not an iteminfo mod")


# ============================================================
#   Legacy JSON apply (resolves entry+rel_offset via BlobStart map)
# ============================================================

def _apply_legacy_json(vanilla_bytes: bytes, mod_json: dict,
                      entry_blob_start: dict
                      ) -> tuple[bytes, int, int, list[bool]]:
    """Apply legacy byte patches to a copy of vanilla bytes. Resolves
    entry+rel_offset via the BlobStart map (BuffPatcher.find_items output).

    Returns (modded_bytes, applied, skipped, per_change_mask). The mask
    is parallel to the doc's flattened `changes` list (in the order
    produced by `iteminfo_inspector.collect_iteminfo_patches`) and is
    True when that specific patch landed. The Inspector uses the mask to
    tag patches as PATCH_STALE vs PATCH_APPLIED. Skipped = patches whose
    'original' didn't match current bytes — stale mod for different game
    version — plus unsupported change types and malformed hex.
    """
    buf = bytearray(vanilla_bytes)
    applied = 0
    skipped = 0
    mask: list[bool] = []
    for patch in mod_json.get("patches", []):
        gf = (patch.get("game_file") or "").lower()
        if "iteminfo.pabgb" not in gf:
            continue
        for change in patch.get("changes", []):
            ctype = change.get("type", "replace")
            if ctype != "replace":
                skipped += 1
                mask.append(False)
                continue
            def _parse_off(v):
                # JMM format:2 JSON writes offsets as hex strings without a
                # 0x prefix (e.g. "3EEF91"). Older tools sometimes wrote
                # decimal ints. Accept both — try hex first for strings,
                # fall back to decimal.
                if isinstance(v, int):
                    return v
                s = str(v).strip()
                if s.lower().startswith("0x"):
                    return int(s, 16)
                try:
                    return int(s, 16)
                except ValueError:
                    return int(s)

            if "offset" in change:
                off = _parse_off(change["offset"])
            elif "entry" in change and "rel_offset" in change:
                base = entry_blob_start.get(change["entry"])
                if base is None:
                    skipped += 1
                    mask.append(False)
                    continue
                off = base + _parse_off(change["rel_offset"])
            else:
                skipped += 1
                mask.append(False)
                continue
            orig_hex = change.get("original", "")
            new_hex = change.get("patched", "")
            if not new_hex:
                skipped += 1
                mask.append(False)
                continue
            try:
                orig = bytes.fromhex(orig_hex)
                new = bytes.fromhex(new_hex)
            except ValueError:
                skipped += 1
                mask.append(False)
                continue
            if orig and buf[off:off+len(orig)] != orig:
                skipped += 1
                mask.append(False)
                continue
            buf[off:off+len(new)] = new
            applied += 1
            mask.append(True)
    return bytes(buf), applied, skipped, mask


# ============================================================
#   Dict-level merge (field by field)
# ============================================================

def _walk_leaves(d, prefix=""):
    out = []
    if isinstance(d, dict):
        for k, v in d.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                out.extend(_walk_leaves(v, p))
            else:
                out.append((p, v))
    return out

def _get_path(d, path):
    cur = d
    for part in path.split("."):
        if cur is None or not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur

def _set_path(d, path, value):
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value


@dataclass
class FieldConflict:
    entry_key: str
    field_path: str
    winner_mod: str
    loser_mod: str
    winner_value: object
    loser_value: object
    # Optional per-side patch index, populated when the mod is a legacy
    # JSON whose inspector resolved an individual byte patch to this
    # field. Lets the log say "patch #7 in mod A beat patch #3 in mod B
    # for Item_X.cooltime" instead of just naming the mods.
    winner_patch_index: Optional[int] = None
    loser_patch_index: Optional[int] = None


def _merge_entries(vanilla_entry: dict, mod_entries: list[tuple[str, dict]]
                   ) -> tuple[dict, list[FieldConflict]]:
    merged = json.loads(json.dumps(vanilla_entry))
    conflicts: list[FieldConflict] = []
    field_author: dict[str, str] = {}
    for mod_name, mod_entry in mod_entries:
        if mod_entry is None:
            continue
        for path, mod_val in _walk_leaves(mod_entry):
            van_val = _get_path(vanilla_entry, path)
            if mod_val == van_val:
                continue
            prev = field_author.get(path)
            if prev and _get_path(merged, path) != mod_val:
                conflicts.append(FieldConflict(
                    entry_key=str(vanilla_entry.get("string_key") or vanilla_entry.get("key") or "?"),
                    field_path=path, winner_mod=mod_name, loser_mod=prev,
                    winner_value=mod_val,
                    loser_value=_get_path(merged, path),
                ))
            _set_path(merged, path, mod_val)
            field_author[path] = mod_name
    return merged, conflicts


def _attach_patch_provenance(conflicts: list[FieldConflict],
                              mods: list) -> None:
    """Post-merge: look up each conflict's winner/loser mod and, if that
    mod is a legacy JSON with resolved inspections, find the patch index
    that targeted this field. Mutates conflicts in place.

    Handles multiple patches to the same field within one JSON by
    returning the *last* such index (matches last-writer-wins semantics
    of the byte-apply). None if no match."""
    by_name = {m.name: m for m in mods}

    def lookup(mod_name: str, entry_key: str, field_path: str) -> Optional[int]:
        m = by_name.get(mod_name)
        if m is None or m.kind != "legacy_json" or not m.inspections:
            return None
        last_hit: Optional[int] = None
        for insp in m.inspections:
            if (insp.entry == entry_key
                    and insp.field_path == field_path):
                last_hit = insp.index
        return last_hit

    for c in conflicts:
        c.winner_patch_index = lookup(c.winner_mod, c.entry_key, c.field_path)
        c.loser_patch_index = lookup(c.loser_mod, c.entry_key, c.field_path)


def _merge_all(vanilla_items: list[dict],
               mod_lists: list[tuple[str, list[dict]]]
               ) -> tuple[list[dict], list[FieldConflict]]:
    def idx(items): return {it["key"]: it for it in items if "key" in it}
    v_idx = idx(vanilla_items)
    mod_idxs = [(n, idx(items)) for n, items in mod_lists]
    all_keys = set(v_idx.keys())
    for _, m in mod_idxs:
        all_keys.update(m.keys())

    merged_items: list[dict] = []
    conflicts: list[FieldConflict] = []
    for key in sorted(all_keys):
        v = v_idx.get(key)
        mod_entries = [(n, m.get(key)) for n, m in mod_idxs]
        present = [(n, e) for n, e in mod_entries if e is not None]
        if v is None and len(present) == 1:
            merged_items.append(present[0][1])
            continue
        if v is None:
            merged_items.append(present[0][1])
            continue
        merged, confs = _merge_entries(v, mod_entries)
        merged_items.append(merged)
        conflicts.extend(confs)
    return merged_items, conflicts


# ============================================================
#   Drop zone
# ============================================================

class DropZone(QLabel):
    def __init__(self, on_files):
        super().__init__(
            "\n  Drag iteminfo mods here  \n"
            "  (folders with NNNN/0.paz, .pabgb files, or legacy .json mods)  \n")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(110)
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #888; border-radius: 8px;
                font-size: 13px; color: #ccc; background: #2a2a2a;
            }
            QLabel:hover { border-color: #4080FF; color: #fff; }
        """)
        self.setAcceptDrops(True)
        self._cb = on_files

    def dragEnterEvent(self, e: QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:
        paths = [u.toLocalFile() for u in e.mimeData().urls()]
        self._cb(paths)


# ============================================================
#   StackerTab
# ============================================================

class StackerTab(QWidget):
    status_message = Signal(str)
    config_save_requested = Signal()

    def __init__(self, name_db=None, icon_cache=None, config=None,
                 show_guide_fn=None, buffs_tab=None, parent=None):
        super().__init__(parent)
        self._name_db = name_db
        self._icon_cache = icon_cache
        self._config = config if config is not None else {}
        self._show_guide_fn = show_guide_fn
        self._buffs_tab = buffs_tab  # for shared state + Apply to Game
        self._game_path: str = self._config.get("game_install_path", "")
        self._mods: list[ModEntry] = []
        self._merged_items: list = []
        self._conflicts: list[FieldConflict] = []
        self._build_ui()

    def set_game_path(self, path: str) -> None:
        self._game_path = path or ""
        if hasattr(self, "_game_edit"):
            self._game_edit.setText(self._game_path)

    # ------------------------------------------------------------
    # Styling palette — matches JMM's dark look (MainWindow.xaml).
    # Colours are hardcoded so the tab stays consistent across themes
    # and does not inherit whatever the rest of the app is rendering in.
    _BG        = "#111218"
    _PANEL_BG  = "#1A1D24"
    _PANEL_HDR = "#1A1D28"
    _BORDER    = "#2A3040"
    _ROW_ALT   = "#16181F"
    _TXT       = "#C8CCD8"
    _TXT_DIM   = "#8C91A0"
    _TXT_MUTED = "#5A6070"
    _ACCENT_G  = "#32B450"   # green — active / success
    _ACCENT_R  = "#C83232"   # red   — destructive
    _ACCENT_B  = "#3C78DC"   # blue  — info / export
    _ACCENT_O  = "#DC961E"   # orange — warn / filter

    def _build_ui(self):
        """3-column layout, JMM-style.

        Column 1 — SOURCES: list of all dropped mods + the ItemBuffs
        snapshot. Each row has a toggle (enable/disable), name, type tag,
        and a delete button. Disabled rows are skipped in the merge but
        stay visible.

        Column 2 — DETAILS: info about the currently-selected source
        (kind, path, entry count, staged sibling files, conflict list
        after Preview/Apply). Replaces the old standalone conflicts
        table — conflicts now show inline with the source that won.

        Column 3 — LOG: monospace progress log, unchanged semantics.

        Top strip: header + game path + dropzone.
        Bottom strip: primary action buttons (Preview / Apply / Export)
        and secondary actions (Pull / Remove Stack / Hand off).
        """
        self.setMinimumWidth(900)
        self.setMinimumHeight(560)
        self.setStyleSheet(self._build_qss())

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 10)
        v.setSpacing(8)

        # --- Top strip: title --------------------------------------------
        title_row = QHBoxLayout()
        title = QLabel("STACKER TOOL")
        title.setObjectName("stackerTitle")
        subtitle = QLabel("Merge N iteminfo.pabgb mods into ONE clean overlay")
        subtitle.setObjectName("stackerSubtitle")
        title_row.addWidget(title)
        title_row.addSpacing(10)
        title_row.addWidget(subtitle)
        title_row.addStretch(1)
        v.addLayout(title_row)

        # --- Game dir row (flat bar, JMM-style) ---------------------------
        game_bar = QFrame()
        game_bar.setObjectName("gameBar")
        gbl = QHBoxLayout(game_bar)
        gbl.setContentsMargins(10, 5, 5, 5)
        gbl.setSpacing(6)
        gp_lbl = QLabel("GAME DIR")
        gp_lbl.setObjectName("gameLbl")
        gbl.addWidget(gp_lbl)
        self._game_edit = QLineEdit(self._game_path)
        self._game_edit.setPlaceholderText(
            r"C:\Program Files (x86)\Steam\steamapps\common\Crimson Desert")
        self._game_edit.setObjectName("gameEdit")
        self._game_edit.setMinimumHeight(26)
        gbl.addWidget(self._game_edit, stretch=1)
        browse = _size_button(QPushButton("Browse"))
        browse.setObjectName("flatBtn")
        browse.clicked.connect(self._pick_game_dir)
        gbl.addWidget(browse)
        v.addWidget(game_bar)

        # --- Drop zone --------------------------------------------------
        self._drop = DropZone(self._add_files)
        v.addWidget(self._drop)

        # --- Main 3-column splitter ------------------------------------
        main = QSplitter(Qt.Horizontal)
        main.setChildrenCollapsible(False)
        main.setHandleWidth(6)
        main.setObjectName("mainSplitter")

        # Column 1: Sources
        col1 = self._build_sources_panel()
        main.addWidget(col1)

        # Column 2: Details / Conflicts
        col2 = self._build_details_panel()
        main.addWidget(col2)

        # Column 3: Log
        col3 = self._build_log_panel()
        main.addWidget(col3)

        main.setStretchFactor(0, 3)
        main.setStretchFactor(1, 3)
        main.setStretchFactor(2, 2)
        main.setSizes([360, 360, 260])
        v.addWidget(main, stretch=1)

        # --- Bottom action bar (JMM-style rounded pills) ---------------
        bottom = QFrame()
        bottom.setObjectName("bottomBar")
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(8, 6, 8, 6)
        bl.setSpacing(8)

        self._preview_btn = _size_button(QPushButton("PREVIEW"))
        self._preview_btn.setObjectName("btnNeutral")
        self._preview_btn.setToolTip(
            "Merge all enabled sources in memory, show conflicts, write nothing.")
        self._preview_btn.clicked.connect(lambda: self._run(install=False))
        bl.addWidget(self._preview_btn)

        self._install_btn = _size_button(QPushButton("✔ APPLY STACK"))
        self._install_btn.setObjectName("btnPrimary")
        self._install_btn.setToolTip(
            f"Merge all enabled sources and write one overlay at "
            f"<game>/{OVERLAY_GROUP_DEFAULT}/.")
        self._install_btn.clicked.connect(lambda: self._run(install=True))
        bl.addWidget(self._install_btn)

        self._export_btn = _size_button(QPushButton("📦 EXPORT MOD"))
        self._export_btn.setObjectName("btnExport")
        self._export_btn.setToolTip(
            "Merge all enabled sources and write a standard compiled folder "
            "mod to a path you pick. Does NOT touch the game folder.")
        self._export_btn.clicked.connect(lambda: self._run(install=False, export=True))
        bl.addWidget(self._export_btn)

        bl.addStretch(1)

        self._uninstall_btn = _size_button(QPushButton("✖ REMOVE STACK"))
        self._uninstall_btn.setObjectName("btnDestructive")
        self._uninstall_btn.setToolTip(
            f"Delete <game>/{OVERLAY_GROUP_DEFAULT}/ and drop its PAPGT entry. "
            "Does not touch JMM/ItemBuffs overlays.")
        self._uninstall_btn.clicked.connect(self._uninstall_stack)
        bl.addWidget(self._uninstall_btn)

        self._send_buffs_btn = _size_button(QPushButton("→ HAND OFF"))
        self._send_buffs_btn.setObjectName("flatBtn")
        self._send_buffs_btn.setToolTip(
            "Load the merged dict list into ItemBuffs for hand-tuning.")
        self._send_buffs_btn.clicked.connect(self._send_to_buffs)
        bl.addWidget(self._send_buffs_btn)

        v.addWidget(bottom)

    # ------------------------------------------------------------
    def _build_qss(self) -> str:
        """Inline stylesheet — matches JMM's dark look. Scoped via object
        names so it doesn't leak to sibling tabs."""
        return f"""
        StackerTab {{
            background: {self._BG};
            color: {self._TXT};
        }}
        QLabel#stackerTitle {{
            color: {self._ACCENT_R};
            font-family: "Segoe UI";
            font-size: 16px;
            font-weight: bold;
            padding: 2px 4px;
        }}
        QLabel#stackerSubtitle {{
            color: {self._TXT_DIM};
            font-size: 11px;
            padding: 2px 4px;
        }}
        QFrame#gameBar, QFrame#bottomBar {{
            background: {self._PANEL_HDR};
            border: 1px solid {self._BORDER};
            border-radius: 5px;
        }}
        QLabel#gameLbl {{
            color: {self._ACCENT_O};
            font-size: 10px;
            font-weight: bold;
            padding-right: 4px;
        }}
        QLineEdit#gameEdit {{
            background: {self._BG};
            color: {self._ACCENT_G};
            border: 1px solid {self._BORDER};
            border-radius: 4px;
            padding: 4px 8px;
            selection-background-color: #2A3050;
        }}
        QSplitter#mainSplitter::handle {{
            background: {self._BORDER};
        }}
        QFrame#sourcesPanel, QFrame#detailsPanel, QFrame#logPanel {{
            background: {self._PANEL_BG};
            border: 1px solid {self._BORDER};
            border-radius: 6px;
        }}
        QLabel[role="panelHeader"] {{
            background: {self._PANEL_HDR};
            color: {self._TXT_DIM};
            font-size: 11px;
            font-weight: bold;
            padding: 7px 10px;
            border: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
        }}
        QLabel#patchesHeader {{
            color: {self._ACCENT_O};
        }}
        QLabel#logHeader {{
            color: {self._TXT_MUTED};
        }}
        QListWidget#sourcesList {{
            background: {self._PANEL_BG};
            color: {self._TXT};
            border: none;
            outline: 0;
            font-family: "Segoe UI";
            font-size: 13px;
        }}
        QListWidget#sourcesList::item {{
            border-bottom: 1px solid #252838;
            padding: 0;
        }}
        QListWidget#sourcesList::item:selected {{
            background: #242838;
            border-left: 3px solid {self._ACCENT_B};
        }}
        QListWidget#sourcesList::item:hover {{
            background: #1E2230;
        }}
        QTextEdit#detailsText, QTextEdit#logText {{
            background: #14161E;
            color: {self._TXT_DIM};
            border: none;
            font-family: "Consolas", "Cascadia Code", monospace;
            font-size: 12px;
            padding: 6px;
        }}
        QPushButton {{
            font-family: "Segoe UI";
            font-size: 12px;
            font-weight: 600;
            border-radius: 4px;
            padding: 6px 14px;
            background: #28303E;
            color: {self._TXT};
            border: 1px solid #30384A;
        }}
        QPushButton:hover {{ background: #343C4E; }}
        QPushButton:pressed {{ background: #1E2430; }}
        QPushButton:disabled {{ background: #202430; color: #555; border-color: #2A3040; }}
        QPushButton#flatBtn {{
            background: #1A1D2A;
            color: {self._TXT_DIM};
            border: 1px solid #2A3040;
        }}
        QPushButton#flatBtn:hover {{ background: #2C3040; color: {self._TXT}; }}
        QPushButton#btnNeutral {{
            background: #464B5A;
            color: #FFFFFF;
            border: 1px solid #565B6A;
            padding: 7px 16px;
        }}
        QPushButton#btnPrimary {{
            background: {self._ACCENT_G};
            color: #0E1018;
            border: 1px solid #42C460;
            font-weight: bold;
            padding: 7px 18px;
        }}
        QPushButton#btnPrimary:hover {{ background: #42C460; }}
        QPushButton#btnExport {{
            background: {self._ACCENT_B};
            color: #FFFFFF;
            border: 1px solid #4C88EC;
            font-weight: bold;
            padding: 7px 18px;
        }}
        QPushButton#btnExport:hover {{ background: #4C88EC; }}
        QPushButton#btnDestructive {{
            background: {self._ACCENT_R};
            color: #FFFFFF;
            border: 1px solid #D84242;
            padding: 7px 14px;
        }}
        QPushButton#btnDestructive:hover {{ background: #D84242; }}
        QToolButton#rowDelete {{
            background: #2A2030;
            color: {self._TXT_DIM};
            border: 1px solid #3A3E52;
            border-radius: 3px;
            padding: 2px 8px;
            font-weight: bold;
        }}
        QToolButton#rowDelete:hover {{
            background: #6A2030;
            color: #FF6060;
        }}
        QCheckBox {{ color: {self._TXT}; }}
        QCheckBox::indicator {{
            width: 16px; height: 16px;
        }}
        """

    # ------------------------------------------------------------
    def _panel_header(self, text: str, obj_name: str = "") -> QLabel:
        """A JMM-style panel header row — flat, bold small-caps."""
        lbl = QLabel(text)
        lbl.setProperty("role", "panelHeader")
        if obj_name:
            lbl.setObjectName(obj_name)
        return lbl

    # ------------------------------------------------------------
    def _build_sources_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sourcesPanel")
        frame.setMinimumWidth(280)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = self._panel_header("SOURCES  —  drag mods above or use Add")
        lay.addWidget(header)

        self._mods_list = QListWidget()
        self._mods_list.setObjectName("sourcesList")
        self._mods_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._mods_list.setAlternatingRowColors(False)
        self._mods_list.setUniformItemSizes(False)
        self._mods_list.currentRowChanged.connect(self._on_source_selected)
        lay.addWidget(self._mods_list, stretch=1)

        # Toolbar at the bottom of the sources panel
        bar = QFrame()
        bar.setStyleSheet(
            f"QFrame {{ background: {self._PANEL_HDR}; "
            f"border-bottom-left-radius: 6px; border-bottom-right-radius: 6px; }}")
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(6, 6, 6, 6)
        blay.setSpacing(4)

        add_btn = _size_button(QPushButton("+ Add"))
        add_btn.setObjectName("flatBtn")
        add_btn.clicked.connect(self._pick_files)
        blay.addWidget(add_btn)

        pull_btn = _size_button(QPushButton("⇅ Pull Buffs"))
        pull_btn.setObjectName("flatBtn")
        pull_btn.setToolTip(
            "Snapshot the current ItemBuffs tab edits (dict edits, Max Stacks, "
            "Inf Durability, cooldowns, transmog, VFX, staged skill/equipslot "
            "files) as a stack source. Clicking again replaces the prior snapshot.")
        pull_btn.clicked.connect(self._pull_from_itembuffs)
        blay.addWidget(pull_btn)

        blay.addStretch(1)

        rm_btn = _size_button(QPushButton("Remove"))
        rm_btn.setObjectName("flatBtn")
        rm_btn.clicked.connect(self._remove_selected)
        blay.addWidget(rm_btn)

        clr_btn = _size_button(QPushButton("Clear"))
        clr_btn.setObjectName("flatBtn")
        clr_btn.clicked.connect(self._clear_all)
        blay.addWidget(clr_btn)

        lay.addWidget(bar)
        return frame

    # ------------------------------------------------------------
    def _build_details_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("detailsPanel")
        frame.setMinimumWidth(260)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._panel_header(
            "DETAILS  —  selected source + merge conflicts",
            obj_name="patchesHeader"))

        self._details = QTextEdit()
        self._details.setObjectName("detailsText")
        self._details.setReadOnly(True)
        self._details.setLineWrapMode(QTextEdit.WidgetWidth)
        self._details.setPlainText(
            "Select a source in the left panel to see its details here.\n\n"
            "After clicking Preview or Apply Stack, this panel shows any\n"
            "field-level conflicts between sources and which one won.")
        lay.addWidget(self._details, stretch=1)
        return frame

    # ------------------------------------------------------------
    def _build_log_panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("logPanel")
        frame.setMinimumWidth(220)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        lay.addWidget(self._panel_header("LOG", obj_name="logHeader"))

        self._log = QTextEdit()
        self._log.setObjectName("logText")
        self._log.setReadOnly(True)
        self._log.setLineWrapMode(QTextEdit.NoWrap)
        lay.addWidget(self._log, stretch=1)
        return frame

    # ------------------------------------------------------------
    def _pick_game_dir(self):
        d = QFileDialog.getExistingDirectory(
            self, "Pick Crimson Desert game folder", self._game_edit.text() or "")
        if d:
            self._game_edit.setText(d)
            self._config["game_install_path"] = d
            self.config_save_requested.emit()

    def _pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Pick mod files",
            "", "Mod files (*.json *.pabgb);;All files (*.*)")
        if paths:
            self._add_files(paths)

    def _add_files(self, paths: list[str]):
        for p in paths:
            entry = _classify(p)
            self._mods.append(entry)
            self._append_mod_row(entry)

    def _pull_from_itembuffs(self):
        """Snapshot the full ItemBuffs tab state — all four buckets.

        A: _buff_rust_items (dict-level edits)
        B: _stack_check state + _stack_spin value, _inf_dura_check state
        C: _cd_patches, _transmog_swaps, _vfx_* lists (post-serialize byte patches)
        D: _staged_skill_files, _staged_equip_files (sibling files for the
           same overlay PAZ — skill.pabgb/pabgh + equipslotinfo.pabgb/pabgh)

        Replaces any prior itembuffs snapshot so repeated clicks refresh
        rather than accumulate.
        """
        if self._buffs_tab is None:
            QMessageBox.warning(self, "Stacker Tool",
                "ItemBuffs tab not available.")
            return
        items = getattr(self._buffs_tab, "_buff_rust_items", None)
        if not items:
            QMessageBox.information(self, "Stacker Tool",
                "ItemBuffs has no items loaded yet. Open ItemBuffs, click "
                "Extract Iteminfo, make your edits, then come back and click "
                "Pull again.")
            return

        bt = self._buffs_tab

        # Drop any previous itembuffs snapshot so we don't double-count
        self._mods = [m for m in self._mods if m.kind != "itembuffs_edits"]

        # Bucket A — deep copy the parsed dict list so later ItemBuffs
        # edits don't mutate our capture.
        snap_items = copy.deepcopy(items)

        # Bucket B — checkbox toggles. These are only applied to
        # _buff_rust_items at the moment ItemBuffs's Apply runs. We
        # capture them separately so our merge can apply them before
        # serialize, regardless of whether ItemBuffs itself has run Apply.
        apply_stacks_val: Optional[int] = None
        if (hasattr(bt, "_stack_check") and bt._stack_check is not None
                and bt._stack_check.isChecked()
                and hasattr(bt, "_stack_spin") and bt._stack_spin is not None):
            apply_stacks_val = bt._stack_spin.value()

        apply_inf_dura_val: bool = (
            hasattr(bt, "_inf_dura_check") and bt._inf_dura_check is not None
            and bt._inf_dura_check.isChecked())

        # Bucket C — post-serialize byte patches. Deep-copy to decouple.
        cd_snap = copy.deepcopy(getattr(bt, "_cd_patches", {}) or {})
        transmog_snap = copy.deepcopy(getattr(bt, "_transmog_swaps", []) or [])
        vfx_size_snap = copy.deepcopy(getattr(bt, "_vfx_size_changes", []) or [])
        vfx_swap_snap = copy.deepcopy(getattr(bt, "_vfx_swaps", []) or [])
        vfx_anim_snap = copy.deepcopy(getattr(bt, "_vfx_anim_swaps", []) or [])
        vfx_attach_snap = copy.deepcopy(getattr(bt, "_vfx_attach_changes", []) or [])

        # Bucket D — sibling file bytes. These are already raw bytes in
        # ItemBuffs's state, so a shallow copy of the dict is enough;
        # the bytes objects themselves are immutable.
        staged_skill_snap = dict(getattr(bt, "_staged_skill_files", None) or {})
        staged_equip_snap = dict(getattr(bt, "_staged_equip_files", None) or {})

        # Scan the dict so we can tell the user *what* is in the 6024
        # entries — not just "6024 entries". Surfaces UP v2 tribe edits,
        # Make Dyeable, No Cooldown (dict-level, not _cd_patches), etc.
        feats = _detect_dict_features(snap_items)

        # ------------------------------------------------------------
        # UP v2 equipslotinfo auto-stage fallback.
        #
        # _eb_enable_everything_oneclick and _eb_universal_proficiency_v2
        # both stage equipslotinfo into _staged_equip_files — BUT their
        # equipslotinfo serializer is wrapped in a try/except that
        # silently fails if crimson_rs.extract_file can't find 0008
        # (wrong game path, missing install, read error). Users who hit
        # that path get tribe_gender edits in the dict but no
        # equipslotinfo bundle, which means the game rejects the UP v2
        # feature in-game even though the dict *looks* right.
        #
        # At Pull time: if the dict has UP v2 tribe edits AND staged
        # equipslotinfo is missing, re-run the expansion here from the
        # Stacker's game path. Best-effort — logged but not fatal.
        if feats.get('up_v2_tribe_unioned') and (
                'equipslotinfo.pabgb' not in staged_equip_snap
                or 'equipslotinfo.pabgh' not in staged_equip_snap):
            try:
                generated = self._regenerate_equipslotinfo_for_up_v2()
                if generated:
                    staged_equip_snap.update(generated)
                    self._log_line(
                        f"  ℹ UP v2 tribe edits detected without equipslotinfo "
                        f"bundle — re-staged inline ({len(generated)} files).")
            except Exception as e:
                self._log_line(
                    f"  ⚠ UP v2 tribe edits detected but equipslotinfo stage "
                    f"failed: {e}. Fix game path and click Extract + "
                    f"UP v2 in ItemBuffs, then re-Pull.")

        # Build a readable summary for the Sources table so the user can
        # verify Pull captured what they expect.
        summary_bits = [f"{len(snap_items)} entries"]
        if apply_stacks_val is not None:
            summary_bits.append(f"stacks→{apply_stacks_val}")
        if apply_inf_dura_val:
            summary_bits.append("inf-dura toggle")
        feat_summary = _compact_features_summary(feats)
        if feat_summary:
            summary_bits.append(feat_summary)
        if cd_snap:
            summary_bits.append(f"{len(cd_snap)} cooldown byte-patches")
        if transmog_snap:
            summary_bits.append(f"{len(transmog_snap)} transmog swaps")
        vfx_total = (len(vfx_size_snap) + len(vfx_swap_snap)
                     + len(vfx_anim_snap) + len(vfx_attach_snap))
        if vfx_total:
            summary_bits.append(f"{vfx_total} VFX changes")
        if staged_skill_snap:
            summary_bits.append(
                f"skill bundle ({len(staged_skill_snap)} "
                f"file{'s' if len(staged_skill_snap) != 1 else ''})")
        if staged_equip_snap:
            summary_bits.append(
                f"equipslotinfo bundle ({len(staged_equip_snap)} "
                f"file{'s' if len(staged_equip_snap) != 1 else ''})")
        summary = "; ".join(summary_bits)

        entry = ModEntry(
            name="ItemBuffs tab (current edits)",
            path="<in-memory>",
            kind="itembuffs_edits",
            ok=True,
            note=summary,
            parsed_items=snap_items,
            apply_stats="snapshot captured",
            detected_features=feats,
            apply_stacks=apply_stacks_val,
            apply_inf_dura=apply_inf_dura_val,
            cd_patches=cd_snap,
            transmog_swaps=transmog_snap,
            vfx_size_changes=vfx_size_snap,
            vfx_swaps=vfx_swap_snap,
            vfx_anim_swaps=vfx_anim_snap,
            vfx_attach_changes=vfx_attach_snap,
            staged_skill_files=staged_skill_snap,
            staged_equip_files=staged_equip_snap,
        )
        self._mods.append(entry)
        self._refresh_mod_list()
        self._log_line(f"✓ Pulled from ItemBuffs: {summary}.")

    # ------------------------------------------------------------
    def _regenerate_equipslotinfo_for_up_v2(self) -> dict:
        """Re-run the equipslotinfo expansion logic from
        _eb_universal_proficiency_v2 in-process, using the Stacker's
        game path. Returns {filename: bytes} for the two equipslotinfo
        files, or empty dict on failure.

        Called from _pull_from_itembuffs when UP v2 tribe edits were
        detected in the dict but the staged files dict is empty —
        meaning ItemBuffs's equipslotinfo serializer silently failed.
        """
        game = self._game_edit.text().strip() if hasattr(self, "_game_edit") else ""
        if not game or not os.path.isdir(game):
            return {}
        try:
            import crimson_rs
            import equipslotinfo_parser as esp
        except Exception:
            return {}

        try:
            pabgh = bytes(crimson_rs.extract_file(
                game, "0008", INTERNAL_DIR, "equipslotinfo.pabgh"))
            pabgb = bytes(crimson_rs.extract_file(
                game, "0008", INTERNAL_DIR, "equipslotinfo.pabgb"))
        except Exception:
            return {}

        try:
            records = esp.parse_all(pabgh, pabgb)
        except Exception:
            return {}

        # Kliff/Damiane/Oongka player char keys — same constants as the
        # buffs tab (_PLAYER_CHAR_KEYS = {1, 4, 6}).
        player_keys = {1, 4, 6}
        pool: dict = {}
        for rec in records:
            if rec.key not in player_keys:
                continue
            for e in rec.entries:
                key = (e.category_a, e.category_b)
                pool.setdefault(key, set()).update(e.etl_hashes)

        added = 0
        for rec in records:
            if rec.key not in player_keys:
                continue
            for e in rec.entries:
                have = set(e.etl_hashes)
                extra = sorted(pool.get((e.category_a, e.category_b), set()) - have)
                if extra:
                    e.etl_hashes.extend(extra)
                    added += len(extra)

        if added == 0:
            # Already unioned — nothing to bundle (would re-serialize vanilla).
            return {}

        try:
            new_pabgh, new_pabgb = esp.serialize_all(records)
        except Exception:
            return {}

        return {
            "equipslotinfo.pabgh": bytes(new_pabgh),
            "equipslotinfo.pabgb": bytes(new_pabgb),
        }

    # ------------------------------------------------------------
    # Type-tag styling for the Sources list row. Keeps the badge visually
    # distinct at a glance: green for itembuffs, blue for folder mods,
    # amber for legacy JSON, grey for loose pabgb.
    _TYPE_META = {
        "itembuffs_edits": ("ITEMBUFFS", "#1E2A1E", "#4CAF50"),
        "folder_paz":      ("PAZ MOD",   "#1A2030", "#6A9CE0"),
        "loose_pabgb":     ("PABGB",     "#1F2128", "#9EA4B8"),
        "legacy_json":     ("JSON v2",   "#2A2418", "#DC961E"),
    }

    def _append_mod_row(self, m: ModEntry):
        """Create a list row with toggle, name, type tag, and delete."""
        row_widget = self._build_source_row_widget(m)

        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 44))
        # Keep a back-reference to the ModEntry via Qt.UserRole so we
        # can match list positions to the self._mods index even after
        # user-driven reorders.
        item.setData(Qt.UserRole, id(m))
        self._mods_list.addItem(item)
        self._mods_list.setItemWidget(item, row_widget)

    def _build_source_row_widget(self, m: ModEntry) -> QWidget:
        w = QWidget()
        w.setStyleSheet("QWidget { background: transparent; }")
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 4, 6, 4)
        h.setSpacing(8)

        # Toggle — drives m.enabled. Disabled-but-kept rows go semi-dim.
        toggle = QCheckBox()
        toggle.setChecked(bool(m.enabled))
        toggle.setFixedWidth(20)
        toggle.stateChanged.connect(lambda st, me=m: self._set_enabled(me, bool(st)))
        h.addWidget(toggle)

        # Name + status text (two-line label) — stretches to fill.
        name_lbl = QLabel(m.name)
        name_lbl.setStyleSheet(
            f"color: {self._TXT if m.enabled else self._TXT_MUTED}; "
            "font-weight: 600;")
        name_lbl.setToolTip(m.path)
        h.addWidget(name_lbl, stretch=1)

        # Error/status hint — small, right-aligned, shown only if the
        # source failed classification.
        if not m.ok:
            err = QLabel("✘ skipped")
            err.setStyleSheet(f"color: #E06060; font-size: 10px;")
            err.setToolTip(m.note)
            h.addWidget(err)

        # Type tag badge — identifies what kind of source this is.
        kind_txt, bg, fg = self._TYPE_META.get(
            m.kind, ("UNKNOWN", "#2A2A2A", "#888"))
        tag = QLabel(kind_txt)
        tag.setStyleSheet(
            f"background: {bg}; color: {fg}; "
            "font-size: 9px; font-weight: bold; "
            "border: 1px solid #2E3245; border-radius: 3px; "
            "padding: 2px 6px;")
        h.addWidget(tag)

        # Legacy JSON — three-way merge mode cycle. Clicking the button
        # rotates Strict → Semantic → Reparse-Diff → Strict.
        # Strict: byte-apply only (original must match vanilla).
        # Semantic: per-patch field resolution via inspector; stale
        #   patches are skipped but applied ones survive offset drift.
        # Reparse-Diff: splice all patches, reparse, diff. Recovers
        #   stale patches, insert patches, and absolute-offset patches
        #   as a single uniform set of FieldEdits.
        if m.kind == "legacy_json":
            mode_btn = QToolButton()
            mode_btn.setObjectName("rowModeToggle")
            mode_btn.setCursor(Qt.PointingHandCursor)
            mode_btn.setProperty("modeValue", m.merge_mode)
            self._style_mode_btn(mode_btn, m.merge_mode)
            mode_btn.clicked.connect(
                lambda _=False, me=m, btn=mode_btn:
                    self._cycle_merge_mode(me, btn))
            h.addWidget(mode_btn)

        # Per-row delete button (JMM uses ✕)
        del_btn = QToolButton()
        del_btn.setText("✕")
        del_btn.setObjectName("rowDelete")
        del_btn.setCursor(Qt.PointingHandCursor)
        del_btn.setToolTip("Remove this source from the stack")
        del_btn.clicked.connect(lambda _=False, me=m: self._remove_by_ref(me))
        h.addWidget(del_btn)
        return w

    # Mapping used by the three-way toggle. Each entry is
    #   mode_value -> (button_text, bg_color, fg_color, border_color)
    _MODE_META = {
        "strict":       ("STR", "#1F2430", "#9EA4B8", "#2E3245"),
        "semantic":     ("SEM", "#1E3524", "#4CAF50", "#2E8B3E"),
        "reparse_diff": ("RPD", "#2A1F3A", "#C08FE8", "#503E73"),
    }
    _MODE_CYCLE = ["strict", "semantic", "reparse_diff"]

    def _style_mode_btn(self, btn, mode: str) -> None:
        """Set button text + colors to match its current mode value."""
        text, bg, fg, border = self._MODE_META.get(
            mode, self._MODE_META["strict"])
        btn.setText(text)
        btn.setToolTip(
            "Click to cycle merge mode.\n"
            "STR = Strict byte-apply (original must match vanilla).\n"
            "SEM = Semantic field-apply (per-patch field resolution;\n"
            "      survives offset drift from other mods).\n"
            "RPD = Reparse-Diff (splice all, reparse, diff; recovers\n"
            "      stale, insert, and absolute-offset patches uniformly).\n"
            "Requires Preview Merge to re-run.")
        btn.setStyleSheet(
            "QToolButton#rowModeToggle { "
            f"  background: {bg}; color: {fg}; "
            "  font-size: 9px; font-weight: bold; "
            f"  border: 1px solid {border}; border-radius: 3px; "
            "  padding: 2px 6px; }")

    def _cycle_merge_mode(self, m: ModEntry, btn) -> None:
        """Rotate the mode to the next option. Clear cached state so the
        user re-runs Preview — avoids a stale-data pitfall where the
        mode flips but results still reflect the prior mode."""
        try:
            i = self._MODE_CYCLE.index(m.merge_mode)
        except ValueError:
            i = -1
        m.merge_mode = self._MODE_CYCLE[(i + 1) % len(self._MODE_CYCLE)]
        m.inspections = []
        m.reparse_report = None
        m.parsed_items = None
        m.apply_stats = ""
        self._style_mode_btn(btn, m.merge_mode)
        self._refresh_mod_list()

    def _set_enabled(self, m: ModEntry, state: bool) -> None:
        m.enabled = state
        self._refresh_mod_list()

    def _remove_by_ref(self, m: ModEntry) -> None:
        try:
            self._mods.remove(m)
        except ValueError:
            return
        self._refresh_mod_list()

    def _on_source_selected(self, row: int) -> None:
        """Update the middle Details pane when selection changes."""
        if row < 0 or row >= len(self._mods):
            self._refresh_details()
            return
        m = self._mods[row]
        self._refresh_details(selected=m)

    def _remove_selected(self):
        row = self._mods_list.currentRow()
        if row < 0 or row >= len(self._mods):
            return
        self._mods.pop(row)
        self._refresh_mod_list()

    def _clear_all(self):
        self._mods.clear()
        self._mods_list.clear()
        self._merged_items = []
        self._conflicts = []
        self._refresh_details()

    # ------------------------------------------------------------
    def _log_line(self, s: str):
        self._log.append(s)
        QApplication.processEvents()   # flush UI so user sees progress live

    def _run(self, install: bool, export: bool = False):
        game = self._game_edit.text().strip()
        if not game or not os.path.isfile(os.path.join(game, "meta", "0.papgt")):
            QMessageBox.warning(self, "Stacker Tool",
                "Pick a valid Crimson Desert install folder first.\n\n"
                "(Looking for <game>/meta/0.papgt — we still need vanilla to "
                "read from, even when exporting as a standalone mod.)")
            return

        ok_mods = [m for m in self._mods if m.ok and m.kind and m.enabled]
        if not ok_mods:
            any_disabled = any(m.ok and m.kind and not m.enabled for m in self._mods)
            msg = ("Add at least one iteminfo mod first." if not any_disabled else
                   "All sources are currently disabled. Toggle at least one on "
                   "in the Sources panel to include it in the merge.")
            QMessageBox.information(self, "Stacker Tool", msg)
            return

        # If exporting, ask the user where + what name BEFORE doing any work.
        export_target = None
        export_name = None
        if export:
            export_target, export_name = self._ask_export_target()
            if not export_target:
                return

        # Lock buttons during the run so double-clicks don't stack up
        try:
            for b in (self._preview_btn, self._install_btn,
                      self._uninstall_btn, self._export_btn):
                b.setEnabled(False)
            if export:
                self.status_message.emit("Stacker: building folder mod…")
            elif install:
                self.status_message.emit("Stacker: applying…")
            else:
                self.status_message.emit("Stacker: previewing merge…")
            self._log.clear()
            self._log_line(f"Game: {game}")
            self._log_line(f"Sources: {len(ok_mods)}")
            return self._run_inner(install, game, ok_mods,
                                   export_target=export_target,
                                   export_name=export_name)
        finally:
            for b in (self._preview_btn, self._install_btn,
                      self._uninstall_btn, self._export_btn):
                b.setEnabled(True)

    def _run_inner(self, install: bool, game: str, ok_mods: list,
                   export_target: Optional[str] = None,
                   export_name: Optional[str] = None):

        try:
            import crimson_rs
            from paz_patcher import ItemBuffPatcher
        except Exception as e:
            self._log_line(f"✘ crimson_rs/paz_patcher import failed: {e}")
            return

        try:
            vanilla_bytes = bytes(crimson_rs.extract_file(
                game, "0008", INTERNAL_DIR, ITEMINFO_PABGB))
        except Exception as e:
            self._log_line(f"✘ Could not extract vanilla iteminfo: {e}")
            return
        self._log_line(f"  vanilla iteminfo.pabgb: {len(vanilla_bytes):,} bytes")

        try:
            vanilla_items = crimson_rs.parse_iteminfo_from_bytes(vanilla_bytes)
        except Exception as e:
            self._log_line(f"✘ Could not parse vanilla iteminfo: {e}")
            return
        self._log_line(f"  {len(vanilla_items)} entries parsed")

        # Entry BlobStart map for legacy JSON resolve
        entry_blob_start: dict[str, int] = {}
        try:
            patcher = ItemBuffPatcher(game_path=game)
            patcher._original_data = vanilla_bytes
            for rec in patcher.find_items(vanilla_bytes):
                entry_blob_start[rec.name] = rec.data_offset
        except Exception as e:
            self._log_line(f"  (warn) BlobStart map unavailable: {e}")

        # Get per-source parsed items. External mods get extracted-and-parsed;
        # the ItemBuffs snapshot source is already parsed dicts.
        per_mod_parsed: list[tuple[str, list]] = []
        for m in ok_mods:
            try:
                if m.kind == "itembuffs_edits":
                    # Already a list[dict] captured at Pull time. Keep
                    # apply_stats short since the detailed bucket
                    # summary is already in m.note from Pull.
                    items = m.parsed_items or []
                    m.apply_stats = "parsed"
                elif m.kind == "folder_paz":
                    raw = bytes(crimson_rs.extract_file(
                        m.path, m.group, INTERNAL_DIR, ITEMINFO_PABGB))
                    m.effective_pabgb = raw
                    items = crimson_rs.parse_iteminfo_from_bytes(raw)
                    m.parsed_items = items
                    # Pull companion sibling files if this mod ships with
                    # them (UP mods package equipslotinfo; passive-skill
                    # mods package skill.pabgb). Silently ignored when
                    # the PAZ doesn't contain them — most iteminfo mods
                    # don't.
                    companions = []
                    for fname, target in (
                        ("equipslotinfo.pabgb", "staged_equip_files"),
                        ("equipslotinfo.pabgh", "staged_equip_files"),
                        ("skill.pabgb", "staged_skill_files"),
                        ("skill.pabgh", "staged_skill_files"),
                    ):
                        try:
                            comp_raw = bytes(crimson_rs.extract_file(
                                m.path, m.group, INTERNAL_DIR, fname))
                        except Exception:
                            continue
                        if not comp_raw:
                            continue
                        getattr(m, target)[fname] = comp_raw
                        companions.append(fname)
                    m.apply_stats = (
                        f"compiled PAZ unpacked"
                        + (f" (+ {len(companions)} companion"
                           f"{'s' if len(companions) != 1 else ''}: "
                           f"{', '.join(companions)})"
                           if companions else ""))
                elif m.kind == "loose_pabgb":
                    with open(m.path, "rb") as f:
                        m.effective_pabgb = f.read()
                    items = crimson_rs.parse_iteminfo_from_bytes(m.effective_pabgb)
                    m.parsed_items = items
                    m.apply_stats = "loose pabgb loaded"
                elif m.kind == "legacy_json":
                    with open(m.path, encoding="utf-8") as f:
                        doc = json.load(f)
                    # Inspector pass — field attribution for every patch.
                    # Runs BEFORE byte-apply so we have field paths to
                    # surface even for patches that fail bytes-mismatch.
                    changes = iteminfo_inspector.collect_iteminfo_patches(doc)
                    insps = iteminfo_inspector.inspect_patches(
                        vanilla_bytes, changes)
                    m.inspections = insps

                    # Surface corruption-risk signals to the log during
                    # the preview so authors see this even if they don't
                    # open the Details pane. The worst offenders are
                    # __count__ / __len__ / __tag__ writes and split-
                    # primitive patches — all of which byte-patching
                    # cannot do safely across game versions.
                    has_inserts = any(
                        c.get("type") == "insert" for c in changes)
                    danger = iteminfo_inspector.count_dangerous_patches(insps)
                    total_prefix = (danger["count_prefix"]
                                    + danger["len_prefix"]
                                    + danger["tag_prefix"])
                    if (total_prefix or danger["split_primitive"]
                            or has_inserts) and m.merge_mode == "strict":
                        bits = []
                        if danger["count_prefix"]:
                            bits.append(
                                f"{danger['count_prefix']} CArray-count writes")
                        if danger["len_prefix"]:
                            bits.append(
                                f"{danger['len_prefix']} CString-len writes")
                        if danger["tag_prefix"]:
                            bits.append(
                                f"{danger['tag_prefix']} COptional-tag writes")
                        if danger["split_primitive"]:
                            bits.append(
                                f"{danger['split_primitive']} split-primitive patches")
                        if has_inserts:
                            bits.append("uses `insert` patches")
                        self._log_line(
                            f"  ⚠ {m.name}: corruption-risk patterns detected "
                            f"({', '.join(bits)}). "
                            "Switch to Reparse-Diff mode (click STR → RPD on the "
                            "source row) to translate safely.")

                    if m.merge_mode == "reparse_diff":
                        # Splice every patch into vanilla, reparse, diff.
                        # This is the most forgiving mode — it pulls
                        # intent out of stale + insert + absolute-offset
                        # patches uniformly, producing a FieldEdit list
                        # that apply_field_edits writes to the dict tree.
                        items = copy.deepcopy(vanilla_items)
                        report = iteminfo_inspector.reparse_diff_patches(
                            vanilla_bytes, doc, entry_blob_start)
                        m.reparse_report = report
                        r_applied, r_skipped, r_reasons = (
                            iteminfo_inspector.apply_field_edits(
                                items, report.edits))
                        m.parsed_items = items
                        m.effective_pabgb = vanilla_bytes
                        unapplied = len(report.unapplied_patches)
                        parse_break = len(report.parse_break_patches)
                        noop = len(report.no_op_patches)
                        m.apply_stats = (
                            f"reparse-diff ({report.mode}): "
                            f"{r_applied} edits applied, "
                            f"{r_skipped} dict-skip, "
                            f"{unapplied} unapplied, "
                            f"{parse_break} parse-break, "
                            f"{noop} no-op")
                        if parse_break:
                            self._log_line(
                                f"  ⚠ {m.name}: {parse_break} patch(es) "
                                "broke parsing in isolation — those specific "
                                "patches contribute no field edits.")
                    elif m.merge_mode == "semantic":
                        # Per-patch inspector resolution; stale patches
                        # dropped. Survives offset drift for the applied
                        # patches but misses stale ones (unlike
                        # reparse_diff).
                        items = copy.deepcopy(vanilla_items)
                        s_applied, s_skipped, s_reasons = (
                            iteminfo_inspector.apply_semantic(items, insps))
                        m.parsed_items = items
                        m.effective_pabgb = vanilla_bytes
                        m.apply_stats = (
                            f"semantic: {s_applied} field edits, "
                            f"{s_skipped} skipped")
                        if s_skipped and s_reasons:
                            self._log_line(
                                f"  ⚠ {m.name}: {s_skipped} patch(es) "
                                "skipped in semantic mode — see Details.")
                    else:
                        # Strict: legacy byte-apply path. Byte splice +
                        # re-parse of the whole file.
                        modded, applied, skipped, mask = _apply_legacy_json(
                            vanilla_bytes, doc, entry_blob_start)
                        iteminfo_inspector.mark_stale_status(insps, mask)
                        m.effective_pabgb = modded
                        items = crimson_rs.parse_iteminfo_from_bytes(modded)
                        m.parsed_items = items
                        m.apply_stats = f"{applied} applied, {skipped} skipped"
                        if skipped and not applied:
                            self._log_line(
                                f"  ⚠ {m.name}: 0 applied, {skipped} skipped — "
                                f"all patches bytes-mismatch (stale mod for "
                                f"different game version); contributes no edits. "
                                f"Try Reparse-Diff mode — recovers intent from "
                                f"stale patches uniformly.")
                else:
                    continue
                # Attach dict-feature detection to every source so the
                # Details pane shows what's inside regardless of kind.
                # ItemBuffs already had this attached at Pull time; this
                # catches external mods too (useful when the user drops
                # a buff-pack PAZ mod and wants to see what it changes
                # before merging).
                m.detected_features = _detect_dict_features(items)
                per_mod_parsed.append((m.name, items))
                self._log_line(f"  ✓ {m.name}: {m.apply_stats} "
                               f"({len(items)} entries)")
            except Exception as e:
                m.apply_stats = f"error: {e}"
                self._log_line(f"  ✘ {m.name}: {e}")

        # Refresh source rows so updated apply_stats text renders
        self._refresh_mod_list()

        if not per_mod_parsed:
            self._log_line("✘ No mods produced parseable iteminfo.")
            return

        # Dict-level merge
        self._log_line("Merging dict-level…")
        try:
            merged_items, conflicts = _merge_all(vanilla_items, per_mod_parsed)
        except Exception as e:
            self._log_line(f"✘ merge failed: {e}")
            QMessageBox.critical(self, "Stacker Tool",
                f"Merge failed:\n{e}\n\n"
                "One of the sources has an unexpected shape. Try removing "
                "sources one at a time to find which.")
            return
        # For any conflict whose mods are legacy JSONs, try to attribute
        # each side to a specific patch index within that JSON. Lets the
        # conflict log read "patch #7 of A beat patch #3 of B".
        _attach_patch_provenance(conflicts, ok_mods)

        self._merged_items = merged_items
        self._conflicts = conflicts
        self._log_line(f"  merged: {len(merged_items)} entries; "
                       f"{len(conflicts)} field conflict(s)")
        self._refresh_details()

        # Preview-only path — no serialize, no write.
        if not install and not export_target:
            self._log_line("Preview complete. Click Apply Stack to Game, or "
                           "Export as Folder Mod.")
            self.status_message.emit(
                f"Stacker: preview ready ({len(merged_items)} entries, "
                f"{len(conflicts)} conflicts)")
            return

        # ─── Bucket B — apply checkbox toggles to merged dict BEFORE serialize ───
        # Mirrors buffs_v319.py:9821-9842 (_buff_apply_to_game). Any
        # itembuffs_edits source with apply_stacks / apply_inf_dura set
        # mutates the merged dict here. If two sources both have
        # apply_stacks with different targets, the highest wins (user
        # probably wants the more-permissive of the two).
        bucket_b_stacks_target: Optional[int] = None
        bucket_b_inf_dura = False
        for m in ok_mods:
            if m.kind != "itembuffs_edits":
                continue
            if m.apply_stacks is not None:
                if bucket_b_stacks_target is None or m.apply_stacks > bucket_b_stacks_target:
                    bucket_b_stacks_target = m.apply_stacks
            if m.apply_inf_dura:
                bucket_b_inf_dura = True

        if bucket_b_stacks_target is not None:
            n = 0
            for it in merged_items:
                if it.get("max_stack_count", 1) > 1:
                    it["max_stack_count"] = bucket_b_stacks_target
                    n += 1
            self._log_line(f"  Max Stacks: set max_stack_count={bucket_b_stacks_target} on {n} items")
        if bucket_b_inf_dura:
            n = 0
            for it in merged_items:
                endurance = it.get("max_endurance", 0)
                if endurance > 0 and endurance != 65535:
                    it["max_endurance"] = 65535
                    it["is_destroy_when_broken"] = 0
                    n += 1
            self._log_line(f"  Infinite Durability: max_endurance=65535 on {n} items")

        # Serialize the merged dict list back to pabgb bytes
        self._log_line("Serializing merged iteminfo…")
        try:
            merged_bytes = crimson_rs.serialize_iteminfo(merged_items)
        except Exception as e:
            self._log_line(f"✘ serialize_iteminfo failed: {e}")
            QMessageBox.critical(self, "Stacker Tool",
                f"Serialize failed:\n{e}\n\n"
                "This usually means one of the merged entries has an invalid "
                "field type. Nothing was written.")
            return
        self._log_line(f"  {len(merged_bytes):,} bytes")

        # ─── Bucket C — apply post-serialize byte patches ───
        # Mirrors buffs_v319.py:9845-9879 ordering: VFX → cooldowns → transmog.
        # Each itembuffs_edits source contributes independently; per-item
        # conflicts are resolved last-writer-wins (install order).
        merged_bytes = self._apply_bucket_c(merged_bytes, ok_mods)

        # Sibling files (Bucket D) — collected here, passed to pack step.
        sibling_files = self._collect_bucket_d(ok_mods)

        # Split equipslotinfo OUT of the main overlay bundle -- UP v2 only
        # works when equipslotinfo ships as a separate overlay group from
        # iteminfo. See OVERLAY_EQUIP_GROUP_DEFAULT docstring for why.
        equip_files = {}
        for _k in list(sibling_files.keys()):
            if _k.startswith("equipslotinfo."):
                equip_files[_k] = sibling_files.pop(_k)
        if equip_files:
            self._log_line(
                f"  Equipslotinfo split into its own overlay group: "
                f"{{{', '.join(sorted(equip_files.keys()))}}}")

        # Branch: export as standalone folder mod, OR install directly.
        if export_target:
            try:
                out_dir = self._pack_as_folder_mod(
                    export_target, export_name, merged_bytes,
                    source_count=len(per_mod_parsed),
                    conflict_count=len(conflicts),
                    sibling_files=sibling_files,
                    equip_files=equip_files)
            except Exception as e:
                self._log_line(f"✘ export failed: {e}")
                QMessageBox.critical(self, "Stacker Tool",
                    f"Export failed:\n{e}")
                return
            self._log_line(f"✔ Exported folder mod to: {out_dir}")
            self.status_message.emit(
                f"Stacker: exported {export_name} "
                f"({len(per_mod_parsed)} sources merged)")
            reply = QMessageBox.information(
                self, "Stacker Tool",
                f"✔ Built folder mod at:\n\n{out_dir}\n\n"
                f"  {len(per_mod_parsed)} source(s) merged.\n"
                f"  {len(conflicts)} field conflict(s) auto-resolved.\n\n"
                "Install it with any loader (JMM, CDUMM, etc.) the same way "
                "you'd install any other folder mod, OR zip it and share it "
                "on Nexus as a standalone merged pack.\n\n"
                "Open the folder now?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                try:
                    os.startfile(out_dir)
                except Exception:
                    pass
            return

        # Direct-install path
        group = OVERLAY_GROUP_DEFAULT
        try:
            self._pack_and_install_overlay(game, group, merged_bytes,
                                            sibling_files=sibling_files)
        except Exception as e:
            self._log_line(f"✘ overlay install failed: {e}")
            QMessageBox.critical(self, "Stacker Tool", f"Install failed:\n{e}")
            return

        # If equipslotinfo edits are present, deploy them to the
        # secondary overlay group (stk2/) -- MUST be separate from stk1/
        # for UP v2 to actually work in-game.
        if equip_files:
            try:
                self._pack_and_install_equipslot_overlay(
                    game, OVERLAY_EQUIP_GROUP_DEFAULT, equip_files)
            except Exception as e:
                self._log_line(f"✘ equipslotinfo overlay install failed: {e}")
                QMessageBox.critical(self, "Stacker Tool",
                    f"Equipslotinfo overlay install failed:\n{e}")
                return

        self._log_line(f"✔ Installed. Launch Crimson Desert.")
        self.status_message.emit(
            f"Stacker: installed ({len(per_mod_parsed)} sources merged, "
            f"{len(conflicts)} conflicts auto-resolved)")
        QMessageBox.information(self, "Stacker Tool",
            f"✔ Installed {len(per_mod_parsed)} source(s) as a single overlay at "
            f"<game>/{OVERLAY_GROUP_DEFAULT}/.\n"
            f"{len(conflicts)} field conflict(s) auto-resolved (install order wins).\n\n"
            "Launch the game to verify. If you see issues, click Remove Stack "
            "to revert and try a smaller subset.")

    # ------------------------------------------------------------
    def _apply_bucket_c(self, merged_bytes: bytes, ok_mods: list) -> bytes:
        """Apply post-serialize byte patches from itembuffs_edits sources.

        Order (matches buffs_v319.py:9845-9879):
          1. VFX changes — _apply_vfx_changes on the ItemBuffsTab instance,
             with our captured state swapped in temporarily so the method
             reads from our snapshot instead of current ItemBuffs state.
          2. Cooldown patches — iterate cd_patches per source; use
             ItemBuffsTab._cd_detect to find the offset in the merged
             bytes (detection has to run against the merged bytes, not
             the snapshot bytes, because merge may have shifted layout).
          3. Transmog swaps — same temp-state-swap pattern as VFX.

        Multi-source conflicts: per-item last-writer-wins. A cooldown
        patch from itembuffs source #1 gets overwritten if source #2
        also patches the same item_key's cooldown — logged once.
        """
        bt = self._buffs_tab
        itembuffs_sources = [m for m in ok_mods if m.kind == "itembuffs_edits"]
        if not itembuffs_sources:
            return merged_bytes

        data = bytearray(merged_bytes)

        # --- VFX changes (size, vfx swaps, anim swaps, attach changes) ---
        any_vfx = False
        # Collapse across sources: later source overrides earlier on same item.
        # Since _apply_vfx_changes just iterates lists and writes bytes, we
        # concatenate all sources' lists and let last-writer-wins happen
        # naturally via sequential writes.
        if bt is not None and hasattr(bt, "_apply_vfx_changes"):
            combined_size: list = []
            combined_swaps: list = []
            combined_anim: list = []
            combined_attach: list = []
            for m in itembuffs_sources:
                combined_size += m.vfx_size_changes
                combined_swaps += m.vfx_swaps
                combined_anim += m.vfx_anim_swaps
                combined_attach += m.vfx_attach_changes
            if combined_size or combined_swaps or combined_anim or combined_attach:
                # Swap ItemBuffs state temporarily so _apply_vfx_changes
                # reads from our merged lists. Also force
                # _experimental_mode=True for the duration of the call —
                # _apply_vfx_changes silently returns False if experimental
                # mode is off (buffs_v319.py:3209). If the user pulled VFX
                # changes, we want them applied regardless of the tab's
                # current experimental-mode state.
                orig = (
                    getattr(bt, "_vfx_size_changes", None),
                    getattr(bt, "_vfx_swaps", None),
                    getattr(bt, "_vfx_anim_swaps", None),
                    getattr(bt, "_vfx_attach_changes", None),
                    getattr(bt, "_experimental_mode", False),
                )
                try:
                    bt._vfx_size_changes = combined_size
                    bt._vfx_swaps = combined_swaps
                    bt._vfx_anim_swaps = combined_anim
                    bt._vfx_attach_changes = combined_attach
                    bt._experimental_mode = True
                    if bt._apply_vfx_changes(data):
                        any_vfx = True
                        self._log_line(
                            f"  VFX: {len(combined_size)} size, "
                            f"{len(combined_swaps)} vfx/trails, "
                            f"{len(combined_anim)} anims, "
                            f"{len(combined_attach)} attach")
                finally:
                    (bt._vfx_size_changes, bt._vfx_swaps,
                     bt._vfx_anim_swaps, bt._vfx_attach_changes,
                     bt._experimental_mode) = orig

        # --- Cooldown byte patches ---
        # cd_patches dict: { item_key: (original_off, original_val, new_val) }
        # We coalesce across sources keyed by item_key — last source wins.
        all_cd: dict = {}
        cd_losers: list[tuple] = []
        for m in itembuffs_sources:
            for key, patch in m.cd_patches.items():
                if key in all_cd and all_cd[key] != patch:
                    cd_losers.append((key, all_cd[key], patch, m.name))
                all_cd[key] = patch

        if all_cd and bt is not None and hasattr(bt, "_cd_detect"):
            cd_hit = 0
            for item_key, patch in all_cd.items():
                new_val = patch[2] if isinstance(patch, (tuple, list)) and len(patch) >= 3 else None
                if new_val is None:
                    continue
                try:
                    cd_off, _ = bt._cd_detect(item_key, bytes(data))
                except Exception:
                    cd_off = None
                if cd_off is not None:
                    data[cd_off:cd_off + 4] = struct.pack("<I", new_val)
                    cd_hit += 1
            self._log_line(f"  Cooldown patches: {cd_hit}/{len(all_cd)} applied")
            for key, old, new, winner in cd_losers:
                self._log_line(f"    [CONFLICT] item_key={key}: {winner} overrode earlier cooldown patch")

        # --- Transmog swaps ---
        if bt is not None and hasattr(bt, "_apply_transmog_swaps"):
            combined_transmog: list = []
            for m in itembuffs_sources:
                combined_transmog += m.transmog_swaps
            if combined_transmog:
                orig_transmog = getattr(bt, "_transmog_swaps", None)
                try:
                    bt._transmog_swaps = combined_transmog
                    applied = bt._apply_transmog_swaps(data)
                    self._log_line(
                        f"  Transmog: {applied} byte patches for "
                        f"{len(combined_transmog)} swap(s)")
                finally:
                    bt._transmog_swaps = orig_transmog

        return bytes(data)

    def _collect_bucket_d(self, ok_mods: list) -> dict:
        """Gather staged sibling files from all sources.

        Returns a dict of {filename_within_INTERNAL_DIR: bytes}. Files
        recognized: skill.pabgb, skill.pabgh, equipslotinfo.pabgb,
        equipslotinfo.pabgh. Last source wins if two contribute the
        same filename.

        Sources contributing sibling files:
        - itembuffs_edits (Pull from ItemBuffs): staged by UP v2 /
          passive-skill injection / imbue whitelisting.
        - folder_paz (external mods): captured at parse time when the
          mod's PAZ ships equipslotinfo/skill alongside iteminfo.
          Covers community mods like "UP fork by X" or "passive skill
          pack" that bundle both files.
        """
        collected: dict = {}
        conflicts: list[tuple] = []
        for m in ok_mods:
            # itembuffs_edits + folder_paz can both carry companion
            # files now; loose_pabgb / legacy_json never do.
            if m.kind not in ("itembuffs_edits", "folder_paz"):
                continue
            for bucket in (m.staged_skill_files, m.staged_equip_files):
                for fname, data in bucket.items():
                    if fname in collected and collected[fname] != data:
                        conflicts.append((fname, m.name))
                    collected[fname] = data
        if collected:
            self._log_line(
                f"  Sibling files for overlay: "
                f"{', '.join(sorted(collected.keys()))}")
        for fname, winner in conflicts:
            self._log_line(f"    [CONFLICT] {fname}: {winner}'s version wins")
        return collected

    # ------------------------------------------------------------
    def _ask_export_target(self) -> tuple[Optional[str], Optional[str]]:
        """Prompt user for output location + mod name. Returns
        (parent_dir, mod_name) or (None, None) if cancelled."""
        from PySide6.QtWidgets import QInputDialog

        parent = QFileDialog.getExistingDirectory(
            self, "Pick where to save the merged folder mod",
            self._config.get("stacker_export_dir", os.path.expanduser("~")))
        if not parent:
            return None, None
        self._config["stacker_export_dir"] = parent
        self.config_save_requested.emit()

        default_name = f"CrimsonStack-{time.strftime('%Y%m%d-%H%M%S')}"
        name, ok = QInputDialog.getText(
            self, "Mod name",
            "Folder mod name (a folder with this name will be created):",
            text=default_name)
        if not ok or not name.strip():
            return None, None
        safe = "".join(c if (c.isalnum() or c in "-_ .") else "_"
                       for c in name.strip())
        return parent, safe

    def _pack_as_folder_mod(self, parent_dir: str, mod_name: str,
                            iteminfo_bytes: bytes,
                            source_count: int,
                            conflict_count: int,
                            sibling_files: Optional[dict] = None,
                            equip_files: Optional[dict] = None) -> str:
        """Write a standard compiled folder mod — the same shape every
        Nexus folder-PAZ mod ships as — so any loader (JMM, CDUMM, or
        manual drop) can install it without touching the game folder.

        Output layout:
          <parent_dir>/<mod_name>/
            modinfo.json
            0036/
              0.paz    (contains iteminfo.pabgb + optional sibling files)
              0.pamt

        sibling_files lets the caller bundle skill.pabgb/skill.pabgh
        and equipslotinfo.pabgb/equipslotinfo.pabgh alongside iteminfo.
        Used when a pulled ItemBuffs source staged those files (e.g.
        Universal Proficiency v2 stages equipslotinfo).

        No meta/0.papgt is written — the consuming loader rebuilds
        PAPGT when it installs this mod. We're not an installer here.
        """
        import crimson_rs

        sibling_files = sibling_files or {}
        equip_files = equip_files or {}
        out_dir = os.path.join(parent_dir, mod_name)
        if os.path.isdir(out_dir) and os.listdir(out_dir):
            raise RuntimeError(
                f"Output folder '{out_dir}' already exists and is not empty. "
                "Pick a new name or delete it first.")
        os.makedirs(out_dir, exist_ok=True)

        # Standard mod slot — JMM expects numeric group dirs ≥ 36. 0036
        # is the canonical first-mod slot; JMM will remap to any free
        # overlay slot at install time, so there's no conflict with
        # whatever else the user has installed.
        group = "0036"
        group_dir_tmp = None
        with tempfile.TemporaryDirectory() as tmp:
            group_dir_tmp = os.path.join(tmp, group)
            builder = crimson_rs.PackGroupBuilder(
                group_dir_tmp, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE)
            builder.add_file(INTERNAL_DIR, ITEMINFO_PABGB, iteminfo_bytes)
            # Matching pabgh for the merged pabgb: required so the game can
            # look up items by key against our modified layout. Without it,
            # vanilla offsets land mid-entry and post-change items are lost.
            try:
                from item_creator import build_iteminfo_pabgh
                _pabgh = build_iteminfo_pabgh(iteminfo_bytes)
                builder.add_file(INTERNAL_DIR, "iteminfo.pabgh", _pabgh)
            except Exception as _e:
                self._log_line(f"  WARN iteminfo.pabgh regen failed ({_e}) -- export may be broken in-game")
            # Sibling files (Bucket D): skill.pabgb/pabgh, equipslotinfo.*
            # Bundled into the SAME overlay PAZ as iteminfo, same as
            # ItemBuffs's own Apply does at buffs_v319.py:9965-9977.
            for fname, fdata in sibling_files.items():
                builder.add_file(INTERNAL_DIR, fname, fdata)
            # finish() writes 0.paz into group_dir_tmp AND returns pamt bytes
            pamt_bytes = bytes(builder.finish())

            # Copy everything (0.paz + 0.pamt etc.) to out_dir/<group>/
            out_group = os.path.join(out_dir, group)
            os.makedirs(out_group, exist_ok=True)
            for fname in os.listdir(group_dir_tmp):
                shutil.copy2(os.path.join(group_dir_tmp, fname),
                             os.path.join(out_group, fname))
            # builder.finish() may not have written 0.pamt to disk — write
            # it explicitly from the bytes it returned so any loader that
            # reads the file (not just the bytes API) finds it.
            pamt_on_disk = os.path.join(out_group, "0.pamt")
            if not os.path.isfile(pamt_on_disk):
                with open(pamt_on_disk, "wb") as f:
                    f.write(pamt_bytes)

        # Equipslotinfo ships as a SEPARATE overlay group (0037/) --
        # required for UP v2 to work in-game when bundled with iteminfo
        # edits.
        equip_group = "0037"
        equip_pamt = None
        if equip_files:
            with tempfile.TemporaryDirectory() as tmp2:
                equip_build_tmp = os.path.join(tmp2, equip_group)
                eb = crimson_rs.PackGroupBuilder(
                    equip_build_tmp, crimson_rs.Compression.NONE,
                    crimson_rs.Crypto.NONE)
                for fname, fdata in equip_files.items():
                    eb.add_file(INTERNAL_DIR, fname, fdata)
                equip_pamt = bytes(eb.finish())
                out_equip = os.path.join(out_dir, equip_group)
                os.makedirs(out_equip, exist_ok=True)
                for fname in os.listdir(equip_build_tmp):
                    shutil.copy2(os.path.join(equip_build_tmp, fname),
                                 os.path.join(out_equip, fname))
                equip_pamt_on_disk = os.path.join(out_equip, "0.pamt")
                if not os.path.isfile(equip_pamt_on_disk):
                    with open(equip_pamt_on_disk, "wb") as f:
                        f.write(equip_pamt)

        # Build meta/0.papgt so the export folder is drop-in ready:
        # copy the current game's PAPGT, add entries for 0036/ (and
        # 0037/ if equipslotinfo shipped), and ship it alongside. User
        # drops the folder contents into their game dir and everything
        # loads without needing JMM/CDUMM/manual PAPGT surgery.
        game_papgt_path = os.path.join(
            self._game_edit.text().strip(), "meta", "0.papgt")
        papgt_shipped = False
        if os.path.isfile(game_papgt_path):
            try:
                papgt = crimson_rs.parse_papgt_file(game_papgt_path)
                # Drop any existing entries for our groups so we don't dupe
                papgt["entries"] = [
                    e for e in papgt["entries"]
                    if e.get("group_name") not in ("0036", "0037")]
                # Add iteminfo group entry with correct checksum
                iteminfo_checksum = crimson_rs.parse_pamt_bytes(
                    pamt_bytes)["checksum"]
                papgt = crimson_rs.add_papgt_entry(
                    papgt, "0036", iteminfo_checksum, 0, 0x3FFF)
                # Add equipslotinfo group entry if we shipped one
                if equip_pamt is not None:
                    equip_checksum = crimson_rs.parse_pamt_bytes(
                        equip_pamt)["checksum"]
                    papgt = crimson_rs.add_papgt_entry(
                        papgt, "0037", equip_checksum, 0, 0x3FFF)
                meta_dir = os.path.join(out_dir, "meta")
                os.makedirs(meta_dir, exist_ok=True)
                crimson_rs.write_papgt_file(
                    papgt, os.path.join(meta_dir, "0.papgt"))
                papgt_shipped = True
                self._log_line(
                    f"  wrote meta/0.papgt (drop-in; "
                    f"{len(papgt['entries'])} total entries including ours)")
            except Exception as e:
                self._log_line(
                    f"  WARN meta/0.papgt generation failed ({e}) -- "
                    f"export will need JMM/CDUMM to install")

        # modinfo.json — the standard shape JMM / CDUMM look for.
        bundled_files_list = sorted(["iteminfo.pabgb"] + list(sibling_files.keys())
                                    + list(equip_files.keys()))
        overlay_groups = ["0036"] + (["0037"] if equip_files else [])
        # meta/ is added to the folder below (after modinfo init) if we
        # managed to write a PAPGT snapshot. Tracked in papgt_shipped.
        modinfo = {
            "id": mod_name.lower().replace(" ", "_"),
            "name": mod_name,
            "version": "1.0.0",
            "author": "CrimsonGameMods Stacker Tool",
            "description": (
                f"Merged pack built from {source_count} source mod(s). "
                f"{conflict_count} field conflict(s) auto-resolved at merge time. "
                f"Bundles: {', '.join(bundled_files_list)}. "
                "Install this folder with any mod loader that accepts compiled "
                "folder mods (JMM, CDUMM, or manual drop)."),
            "source_count": source_count,
            "conflict_count": conflict_count,
            "bundled_files": bundled_files_list,
            "built_with": "CrimsonGameMods Stacker Tool",
            "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(os.path.join(out_dir, "modinfo.json"), "w", encoding="utf-8") as f:
            json.dump(modinfo, f, indent=2, ensure_ascii=False)

        # README.txt — so users who download the zip understand what it is
        contents_section = ""
        if sibling_files:
            file_list = sorted(sibling_files.keys())
            contents_section = (
                f"\nCONTENTS\n"
                f"--------\n"
                f"This pack bundles the following files into 0036/0.paz:\n\n"
                f"  - iteminfo.pabgb  (merged from all sources)\n"
                + "".join(f"  - {fn}\n" for fn in file_list)
                + f"\nThe extra files above come from ItemBuffs-tab features that\n"
                f"require sibling-file edits alongside iteminfo — for example,\n"
                f"Universal Proficiency needs equipslotinfo to open equip-slot\n"
                f"gates, and passive-skill injections need skill.pabgb/pabgh.\n"
                f"Install the folder as one unit; don't cherry-pick individual\n"
                f"files out of 0036/.\n")

        meta_section = ""
        if papgt_shipped:
            meta_section = (
                "\n"
                "The meta/0.papgt shipped in this folder is a snapshot of YOUR game's\n"
                "PAPGT at export time with our group entries added. Dropping it in\n"
                "will register 0036/ and 0037/ for the game to load.\n"
                "\n"
                "BACKUP your existing meta/0.papgt before replacing -- if you have\n"
                "installed other overlays SINCE this export was built, they will be\n"
                "lost when you overwrite. If in doubt, install via a mod loader.\n")
        readme = (
            f"{mod_name}\n"
            f"{'=' * len(mod_name)}\n\n"
            f"Merged mod pack built from {source_count} source mod(s).\n"
            f"{conflict_count} field-level conflict(s) were auto-resolved at merge time.\n"
            f"{contents_section}\n"
            f"HOW TO INSTALL\n"
            f"--------------\n"
            f"Drop-in (simplest -- only if you trust the meta/0.papgt snapshot):\n"
            f"  Copy the 0036/, 0037/, and meta/ folders into your Crimson Desert\n"
            f"  install directory, replacing meta/0.papgt. Done.\n"
            f"{meta_section}\n"
            f"Via mod loader (safer -- preserves other overlays):\n"
            f"  JMM (CD JSON Mod Manager): drag this folder into mods/_enabled/, Apply.\n"
            f"  CDUMM: import this folder as a mod, enable it, Apply.\n"
            f"\n"
            f"Built by CrimsonGameMods Stacker Tool on {modinfo['built_at']}.\n"
            f"If something doesn't apply correctly, it's most likely because one of\n"
            f"the source mods was built for a different game version.\n")
        with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as f:
            f.write(readme)

        return out_dir

    # ------------------------------------------------------------
    def _pack_and_install_overlay(self, game: str, group: str,
                                  iteminfo_bytes: bytes,
                                  sibling_files: Optional[dict] = None) -> None:
        """Mirror _buff_export_cdumm_mod + _buff_apply_to_game, but for
        the merged iteminfo we computed. Direct write into the game dir.

        sibling_files: optional dict of filename → bytes. These are
        bundled into the same overlay PAZ alongside iteminfo. Used for
        skill.pabgb/pabgh + equipslotinfo.pabgb/pabgh when an
        ItemBuffs source staged them (UP v2, passive skill mods).
        """
        import crimson_rs

        sibling_files = sibling_files or {}

        # Build overlay group PAZ + PAMT
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = os.path.join(tmp, group)
            builder = crimson_rs.PackGroupBuilder(
                build_dir, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE)
            builder.add_file(INTERNAL_DIR, ITEMINFO_PABGB, iteminfo_bytes)
            # Matching pabgh for the merged pabgb (see folder-export site for rationale).
            try:
                from item_creator import build_iteminfo_pabgh
                _pabgh = build_iteminfo_pabgh(iteminfo_bytes)
                builder.add_file(INTERNAL_DIR, "iteminfo.pabgh", _pabgh)
                self._log_line(f"  wrote iteminfo.pabgh ({len(_pabgh):,} bytes) alongside pabgb")
            except Exception as _e:
                self._log_line(f"  WARN iteminfo.pabgh regen failed ({_e}) -- overlay may be broken in-game")
            for fname, fdata in sibling_files.items():
                builder.add_file(INTERNAL_DIR, fname, fdata)
            pamt_bytes = bytes(builder.finish())
            pamt_checksum = crimson_rs.parse_pamt_bytes(pamt_bytes)["checksum"]

            # Copy group files into <game>/NNNN/
            dst_group = os.path.join(game, group)
            if os.path.isdir(dst_group):
                shutil.rmtree(dst_group)
            os.makedirs(dst_group, exist_ok=True)
            for fname in os.listdir(build_dir):
                shutil.copy2(os.path.join(build_dir, fname),
                             os.path.join(dst_group, fname))

        # Update PAPGT: drop any existing entry for this group, add ours
        papgt_path = os.path.join(game, "meta", "0.papgt")
        cur = crimson_rs.parse_papgt_file(papgt_path)
        cur["entries"] = [e for e in cur["entries"]
                          if e.get("group_name") != group]
        cur = crimson_rs.add_papgt_entry(
            cur, group, pamt_checksum, is_optional=0, language=0x3FFF)
        crimson_rs.write_papgt_file(cur, papgt_path)
        bundled_note = ""
        if sibling_files:
            bundled_note = f" (+ {len(sibling_files)} sibling file{'s' if len(sibling_files) != 1 else ''}: {', '.join(sorted(sibling_files.keys()))})"
        self._log_line(f"  wrote overlay {group}/ + updated meta/0.papgt{bundled_note}")

    # ------------------------------------------------------------
    def _pack_and_install_equipslot_overlay(self, game: str, group: str,
                                             equip_files: dict) -> None:
        """Write equipslotinfo.pabgb + .pabgh to a SEPARATE overlay group.

        UP v2 requires split deployment -- bundling equipslotinfo with
        iteminfo in a single overlay group breaks cross-character equips
        (guns on Kliff, blasters on other chars, etc.) even though both
        files individually contain correct data. Split into its own group
        and it works. Confirmed empirically 2026-04-21 vs v1.0.3.
        """
        import crimson_rs

        with tempfile.TemporaryDirectory() as tmp:
            build_dir = os.path.join(tmp, group)
            builder = crimson_rs.PackGroupBuilder(
                build_dir, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE)
            for fname, fdata in equip_files.items():
                builder.add_file(INTERNAL_DIR, fname, fdata)
            pamt_bytes = bytes(builder.finish())
            pamt_checksum = crimson_rs.parse_pamt_bytes(pamt_bytes)["checksum"]

            dst_group = os.path.join(game, group)
            if os.path.isdir(dst_group):
                shutil.rmtree(dst_group)
            os.makedirs(dst_group, exist_ok=True)
            for fname in os.listdir(build_dir):
                shutil.copy2(os.path.join(build_dir, fname),
                             os.path.join(dst_group, fname))

        papgt_path = os.path.join(game, "meta", "0.papgt")
        cur = crimson_rs.parse_papgt_file(papgt_path)
        cur["entries"] = [e for e in cur["entries"]
                          if e.get("group_name") != group]
        cur = crimson_rs.add_papgt_entry(
            cur, group, pamt_checksum, is_optional=0, language=0x3FFF)
        crimson_rs.write_papgt_file(cur, papgt_path)
        self._log_line(
            f"  wrote equipslotinfo overlay {group}/ + updated meta/0.papgt "
            f"({', '.join(sorted(equip_files.keys()))})")

    # ------------------------------------------------------------
    def _refresh_mod_list(self) -> None:
        """Rebuild every row's widget so toggles, names, and tags match
        the current ModEntry state. Row count stays aligned with
        self._mods by construction."""
        sel_row = self._mods_list.currentRow()
        self._mods_list.clear()
        for m in self._mods:
            self._append_mod_row(m)
        if 0 <= sel_row < self._mods_list.count():
            self._mods_list.setCurrentRow(sel_row)
        self._refresh_details()

    def _refresh_details(self, selected: Optional[ModEntry] = None) -> None:
        """Render the middle pane. If a source is selected, show its per-
        source details; always append the merge conflict summary (if any)
        so conflicts are visible without switching selection."""
        lines: list[str] = []

        if selected is not None:
            lines.append(f"● {selected.name}")
            lines.append("─" * 58)
            lines.append(f"Type     : {selected.kind or '(unknown)'}")
            lines.append(f"Enabled  : {'yes' if selected.enabled else 'NO — skipped in merge'}")
            lines.append(f"Path     : {selected.path}")
            if selected.group:
                lines.append(f"Group    : {selected.group}")
            if selected.note:
                lines.append(f"Note     : {selected.note}")
            if selected.apply_stats:
                lines.append(f"Status   : {selected.apply_stats}")
            # ItemBuffs snapshot specifics
            if selected.kind == "itembuffs_edits":
                lines.append("")
                lines.append("BUCKET SUMMARY")
                if selected.parsed_items is not None:
                    lines.append(f"  A — dict entries    : {len(selected.parsed_items)}")
                if selected.apply_stacks is not None:
                    lines.append(f"  B — max stacks      : {selected.apply_stacks}")
                if selected.apply_inf_dura:
                    lines.append(f"  B — inf durability  : yes")
                if selected.cd_patches:
                    lines.append(f"  C — cooldown byte-patches: {len(selected.cd_patches)}")
                if selected.transmog_swaps:
                    lines.append(f"  C — transmog swaps  : {len(selected.transmog_swaps)}")
                vfx_total = (len(selected.vfx_size_changes)
                             + len(selected.vfx_swaps)
                             + len(selected.vfx_anim_swaps)
                             + len(selected.vfx_attach_changes))
                if vfx_total:
                    lines.append(f"  C — VFX changes     : {vfx_total}")
                if selected.staged_skill_files:
                    lines.append(f"  D — skill bundle    : {', '.join(sorted(selected.staged_skill_files))}")
                if selected.staged_equip_files:
                    lines.append(f"  D — equip bundle    : {', '.join(sorted(selected.staged_equip_files))}")

                # Dict-level feature fingerprints — what's *inside* the
                # 6024 entries. Shows the user that UP v2, Make Dyeable,
                # No Cooldown (dict), etc. were all captured even though
                # they don't have dedicated bucket entries.
                feat_lines = _features_summary_lines(selected.detected_features or {})
                if feat_lines:
                    lines.append("")
                    lines.append("DETECTED DICT EDITS (inside Bucket A)")
                    lines.extend(feat_lines)

                # Visible warning if UP v2 tribe edits found but
                # equipslotinfo bundle missing. Auto-fallback in Pull
                # should have caught most cases; this covers the rest.
                if (selected.detected_features.get('up_v2_tribe_unioned')
                        and ('equipslotinfo.pabgb'
                             not in selected.staged_equip_files)):
                    lines.append("")
                    lines.append("⚠ UP v2 tribe edits present but equipslotinfo bundle missing.")
                    lines.append("  Fix: verify the game path is correct, go back to ItemBuffs,")
                    lines.append("  click Extract → Universal Proficiency v2, then re-Pull.")
            # Legacy JSON — inspector readout + mode switch visibility.
            # When a JSON source is selected, surface which patches
            # resolved to which fields, and any stale/missing ones.
            elif selected.kind == "legacy_json":
                lines.append("")
                mode_label = {
                    "strict": "Strict (byte-apply, original must match)",
                    "semantic": "Semantic (per-patch field resolve)",
                    "reparse_diff": "Reparse-Diff (splice-all, reparse, diff)",
                }.get(selected.merge_mode, selected.merge_mode)
                lines.append(f"MERGE MODE : {mode_label}")
                lines.append(
                    "   Click the mode tag (STR/SEM/RPD) in the source row "
                    "to cycle. Requires Preview Merge to re-run.")
                # Inspector readout (present in every mode since it runs
                # up-front before dispatch)
                if selected.inspections:
                    lines.append("")
                    lines.append("INSPECTOR (per-patch field attribution)")
                    lines.extend(
                        iteminfo_inspector.format_inspection_summary(
                            selected.inspections))
                    # Danger warnings — printed BEFORE the per-patch
                    # table so authors see them without scrolling.
                    has_inserts = False
                    if os.path.isfile(selected.path):
                        try:
                            with open(selected.path, encoding="utf-8") as _f:
                                _doc = json.load(_f)
                            has_inserts = any(
                                c.get("type") == "insert"
                                for p in _doc.get("patches") or []
                                for c in p.get("changes") or [])
                        except Exception:
                            pass
                    danger_lines = iteminfo_inspector.format_danger_warnings(
                        selected.inspections, has_inserts=has_inserts)
                    if danger_lines:
                        lines.append("")
                        lines.extend(danger_lines)
                # Reparse-Diff report: only populated when mode ==
                # reparse_diff. Shows the recovered FieldEdits that will
                # flow into the merge.
                rep = getattr(selected, "reparse_report", None)
                if rep is not None:
                    lines.append("")
                    lines.append("REPARSE-DIFF (recovered field edits)")
                    lines.extend(
                        iteminfo_inspector.format_field_edits_summary(rep))
            # External-mod companion bundle surface (folder_paz may
            # carry equipslotinfo / skill in the source PAZ)
            elif selected.kind == "folder_paz":
                if selected.staged_skill_files or selected.staged_equip_files:
                    lines.append("")
                    lines.append("EXTRA FILES BUNDLED FROM MOD")
                    if selected.staged_skill_files:
                        lines.append(f"  skill bundle  : {', '.join(sorted(selected.staged_skill_files))}")
                    if selected.staged_equip_files:
                        lines.append(f"  equip bundle  : {', '.join(sorted(selected.staged_equip_files))}")
            lines.append("")

        # Summary — always shown after selection (if any)
        mods_on = [m for m in self._mods if m.enabled and m.ok and m.kind]
        mods_off = [m for m in self._mods if (not m.enabled) and m.ok and m.kind]
        lines.append(f"STACK — {len(mods_on)} enabled, {len(mods_off)} disabled, "
                     f"{len(self._mods) - len(mods_on) - len(mods_off)} skipped")

        if self._conflicts:
            lines.append("")
            lines.append(f"CONFLICTS — {len(self._conflicts)} field-level "
                         "(install order wins; loser listed beside winner)")
            lines.append("─" * 58)
            # Cap UI to avoid flooding the pane on mega-merges
            for c in self._conflicts[:300]:
                lines.append(f"  {c.entry_key}.{c.field_path}")
                w_tag = (f" (patch #{c.winner_patch_index})"
                         if c.winner_patch_index is not None else "")
                l_tag = (f" (patch #{c.loser_patch_index})"
                         if c.loser_patch_index is not None else "")
                lines.append(f"      ✔ {c.winner_mod}{w_tag} = {c.winner_value!r}")
                lines.append(f"      ✘ {c.loser_mod}{l_tag}  = {c.loser_value!r}")
            if len(self._conflicts) > 300:
                lines.append(f"  … {len(self._conflicts) - 300} more "
                             "(truncated for UI)")
        elif self._merged_items:
            lines.append("")
            lines.append("No field-level conflicts — all sources merged "
                         "without stepping on each other.")

        self._details.setPlainText("\n".join(lines) if lines else
            "Drop mods above, or use + Add / ⇅ Pull Buffs in the Sources panel.")

    # ------------------------------------------------------------
    def _uninstall_stack(self):
        """Remove Stacker's overlay from the game. Deletes <game>/<group>/
        and drops its entry from meta/0.papgt. Other overlays (JMM's or
        ItemBuffs' direct 0058/) are left alone."""
        game = self._game_edit.text().strip()
        if not game or not os.path.isdir(game):
            QMessageBox.warning(self, "Stacker Tool",
                "Pick a valid Crimson Desert install folder first.")
            return

        groups_to_remove = [OVERLAY_GROUP_DEFAULT, OVERLAY_EQUIP_GROUP_DEFAULT]
        group = OVERLAY_GROUP_DEFAULT  # kept for backward compat in log messages
        group_dirs = [(g, os.path.join(game, g)) for g in groups_to_remove]
        papgt_path = os.path.join(game, "meta", "0.papgt")

        overlays_existing = [g for g, d in group_dirs if os.path.isdir(d)]
        papgt_entries_present: list[str] = []
        try:
            import crimson_rs
            if os.path.isfile(papgt_path):
                cur = crimson_rs.parse_papgt_file(papgt_path)
                existing_names = {e.get("group_name") for e in cur.get("entries", [])}
                papgt_entries_present = [g for g in groups_to_remove
                                         if g in existing_names]
        except Exception as e:
            self._log_line(f"  (warn) couldn't inspect PAPGT: {e}")

        if not overlays_existing and not papgt_entries_present:
            QMessageBox.information(self, "Stacker Tool",
                f"No Stacker overlay installed ({' / '.join(g for g, _ in group_dirs)} "
                "don't exist and PAPGT has no entries).")
            return

        confirm = QMessageBox.question(
            self, "Stacker Tool",
            f"Remove Stacker's overlay?\n\n"
            f"  • Delete {group_dir}\n"
            f"  • Drop '{group}' entry from meta/0.papgt\n\n"
            "Other overlays (JMM's, ItemBuffs') are not touched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if confirm != QMessageBox.Yes:
            return

        self._log.clear()
        self._log_line(f"Removing Stacker overlay from {game}…")
        try:
            for g, d in group_dirs:
                if os.path.isdir(d):
                    shutil.rmtree(d)
                    self._log_line(f"  deleted {d}")
            import crimson_rs
            if os.path.isfile(papgt_path):
                cur = crimson_rs.parse_papgt_file(papgt_path)
                before = len(cur["entries"])
                cur["entries"] = [e for e in cur["entries"]
                                  if e.get("group_name") not in groups_to_remove]
                if len(cur["entries"]) != before:
                    crimson_rs.write_papgt_file(cur, papgt_path)
                    self._log_line(
                        f"  dropped {', '.join(groups_to_remove)} from meta/0.papgt "
                        f"({before} → {len(cur['entries'])} entries)")
            self._log_line("✔ Stacker overlay(s) removed.")
            QMessageBox.information(self, "Stacker Tool",
                "Stacker overlay removed. Launch the game to verify.")
        except Exception as e:
            self._log_line(f"✘ Uninstall failed: {e}")
            QMessageBox.critical(self, "Stacker Tool",
                f"Uninstall failed:\n{e}")

    # ------------------------------------------------------------
    def _send_to_buffs(self):
        """Push the merged dict list into ItemBuffs session state so the
        user can hand-tune and re-export through existing buttons."""
        if not self._merged_items:
            QMessageBox.information(self, "Stacker Tool",
                "Run Preview Merge first so there's a merged result to send.")
            return
        if self._buffs_tab is None:
            QMessageBox.warning(self, "Stacker Tool",
                "ItemBuffs tab reference not available.")
            return
        try:
            import crimson_rs
            game = self._game_edit.text().strip()
            vanilla = bytes(crimson_rs.extract_file(
                game, "0008", INTERNAL_DIR, ITEMINFO_PABGB))
            self._buffs_tab._buff_data = bytearray(vanilla)
            self._buffs_tab._buff_rust_items = self._merged_items
            self._buffs_tab._buff_rust_items_original = copy.deepcopy(
                crimson_rs.parse_iteminfo_from_bytes(vanilla))
            self._buffs_tab._buff_rust_lookup = {
                it["key"]: it for it in self._merged_items if "key" in it}
            self._buffs_tab._buff_use_rust = True
            self._buffs_tab._buff_modified = True
            if hasattr(self._buffs_tab, "_rebuild_index"):
                self._buffs_tab._rebuild_index()
            self._log_line(f"✔ Sent {len(self._merged_items)} merged entries to ItemBuffs.")
            QMessageBox.information(self, "Stacker Tool",
                "Merged state loaded into ItemBuffs tab.\n"
                "Switch to ItemBuffs to hand-tune, then use its\n"
                "'Apply to Game' to export.")
        except Exception as e:
            self._log_line(f"✘ send-to-buffs failed: {e}")
