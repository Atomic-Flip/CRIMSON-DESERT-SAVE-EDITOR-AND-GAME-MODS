# Crimson Desert — Iteminfo Modding: Technical Reference

A hands-on guide for anyone building tools that modify `iteminfo.pabgb` (and the related game-data files) in Crimson Desert. Every rule here is backed by empirical testing against the live game — not theory. When this doc says "must" or "will fail", that failure mode has been observed and the trigger is documented.

**Audience:** tool authors, mod developers, reverse engineers.
**Not for:** end users. End users should use the Stacker Tool (`CrimsonGameMods/gui/tabs/stacker.py`) and skip this doc.

---

## 1. File format stack

```
<game>/NNNN/0.paz         container (like a zip; usually uncompressed)
<game>/NNNN/0.pamt        manifest: file names/offsets/checksums inside 0.paz
<game>/meta/0.papgt       master registry: which groups the game loads
```

Inside a `0.paz` you typically find at least two data files per subject:

| File | Purpose |
|---|---|
| `*.pabgb` | Binary blob — sequential item/character/whatever records. The actual data. |
| `*.pabgh` | Key→offset index into the matching pabgb. Format: `u16 count + N × (u32 key, u32 offset_into_pabgb)`. The game uses this to seek directly to a specific item by key. |

Critical implication: **pabgb and pabgh are a pair.** Modifying one without updating the other breaks item lookups silently.

---

## 2. Overlay system

Game data is loaded from "groups" in `<game>/NNNN/` directories, where `NNNN` is a 4-digit name. Vanilla ships groups `0000`–`0035`. Mods add groups in `0036+`. Each group in `meta/0.papgt` has:

- `group_name` (e.g. `"0036"`)
- `pack_meta_checksum` (must match the group's `0.pamt` file's checksum field)
- `language` (usually `0x3FFF` = all)
- `is_optional` (0 for mandatory, 1 for language-specific)

### 2.1 Group names MUST be numeric

The PAPGT loader silently ignores overlay groups with non-numeric names. A group named `stk1`, `mymod`, `patch_v2` will:
- Register in PAPGT cleanly
- Have valid `pack_meta_checksum`
- Sit on disk with correct `0.paz` / `0.pamt`
- **Not load**

Confirmed empirically 2026-04-21. Always use numeric group names for overlays the game must load.

### 2.2 When multiple groups ship the same file

If two overlays both include `iteminfo.pabgb`, only one actually loads — the other is silently shadowed. Priority rules are not fully mapped; empirically later-numbered groups tend to win, but **don't rely on order**. Instead: merge all edits into a single overlay before shipping.

### 2.3 JMM cleanup conflict

JMM's `CleanupStaleOverlayGroups` pass deletes any all-digit group directory not in its manifest. Your overlay at `0062/` will vanish after a JMM Apply cycle. Options:

- Tell users to run your tool **after** JMM, not before.
- Accept re-apply cost.
- Integrate with JMM's manifest so it recognizes your group.

Non-numeric names would survive JMM, but the game won't load them — this tradeoff is unavoidable.

---

## 3. The split-overlay rule (CRITICAL)

**`iteminfo` and `equipslotinfo` MUST ship in separate overlay groups.** Bundling them together in one group breaks Universal Proficiency–style mods (cross-character equippability) even when both files individually contain correct data.

Confirmed empirically 2026-04-21:

| Layout | Muskets on Kliff |
|---|---|
| `0058/` = iteminfo, `0059/` = equipslotinfo (split) | Works |
| `0058/` = iteminfo + equipslotinfo bundled | Broken |

Byte content of both `iteminfo` entries and `equipslotinfo` hashes was byte-identical between test layouts. Only difference: overlay-group packing. The game treats `iteminfo` vs `equipslotinfo` differently based on overlay-group membership. Don't try to outsmart this — always split.

**Rule:**
- Group A: `iteminfo.pabgb` + `iteminfo.pabgh` (+ `skill.pabgb`/`skill.pabgh` — safe to bundle here)
- Group B: `equipslotinfo.pabgb` + `equipslotinfo.pabgh`

In `CrimsonGameMods`:
- ItemBuffs tab → `0058/` + `0059/`
- Stacker Apply Stack → `0062/` + `0063/`
- Stacker Folder Export → `0036/` + `0037/` + `meta/0.papgt` (drop-in)

---

## 4. pabgh regeneration requirement

When your tool produces a modified `iteminfo.pabgb` of any size different from vanilla, you MUST ship a matching `iteminfo.pabgh` regenerated from the new pabgb.

### Why

Vanilla `pabgh` encodes offsets into vanilla `pabgb`'s byte layout. When your mod grows `pabgb` (new sockets, added tribes, inserted items, etc.), vanilla offsets are wrong for the new layout. Every item past the first size change becomes unreachable by key lookup — even though it sits in the file at its new offset, the game seeks to the old offset and reads garbage.

Symptoms: rings show no sockets, muskets disappear from inventory, random items lose their edits. **Not a crash** — silent data corruption where lookup-by-key returns incorrect records.

### How

Regenerate from modded bytes:

```python
import crimson_rs, struct

def build_iteminfo_pabgh(pabgb_bytes: bytes) -> bytes:
    t = crimson_rs.parse_iteminfo_tracked(pabgb_bytes)
    out = bytearray(struct.pack("<H", len(t['items'])))
    for it, sp in zip(t['items'], t['spans']):
        out += struct.pack("<II", it['key'], sp['start'])
    return bytes(out)
```

Verified: rebuilding vanilla pabgh from vanilla pabgb produces a byte-exact match. Implementation lives at `CrimsonGameMods/item_creator.py:build_iteminfo_pabgh`.

---

## 5. Mod format compatibility

### 5.1 Dict-level edits (always safe)

The canonical path: parse `iteminfo.pabgb` into a dict tree, mutate fields on the dict, serialize back with `crimson_rs.serialize_iteminfo(items)`. The serializer derives count prefixes, length prefixes, and presence tags from `len(list)` at write time. Impossible to desync.

```python
import crimson_rs
items = crimson_rs.parse_iteminfo_from_bytes(vanilla_bytes)
for it in items:
    if it.get('string_key') == 'Legendary_Titan_Ring':
        ddd = it.setdefault('drop_default_data', {})
        ddd['use_socket'] = 1
        ddd['add_socket_material_item_list'] = [
            {'item': 1, 'value': 500},
            {'item': 1, 'value': 1000},
            {'item': 1, 'value': 2000},
            {'item': 1, 'value': 3000},
            {'item': 1, 'value': 4000},
        ]
        ddd['socket_valid_count'] = 5
modded_bytes = crimson_rs.serialize_iteminfo(items)
```

No byte-offset math. No count prefixes to bump manually. Always works.

### 5.2 Byte-patch JSON mods (format:2)

Older mods ship JSON with byte-level patches:

```json
{
  "format": 2,
  "patches": [{
    "game_file": "gamedata/iteminfo.pabgb",
    "changes": [
      {"type": "replace", "entry": "Ring_X", "rel_offset": 71,
       "original": "00...", "patched": "01..."},
      {"type": "replace", "offset": "3EEF91",
       "original": "...", "patched": "..."},
      {"type": "insert", "offset": "3F028C", "bytes": "..."}
    ]
  }]
}
```

These can work but have dangerous patterns. See next section.

Offset parsing: JMM-style JSON uses hex strings **without** an `0x` prefix (e.g. `"3EEF91"`). Parse with `int(v, 16)` — never `int(v)`. (This is a real bug we hit on 2026-04-21: `stacker.py` was parsing base-10 and crashing on Ultimate Lantern Reborn.)

---

## 6. Byte-patch corruption vectors

Patches that hit these field-path suffixes will corrupt byte-applied iteminfo:

| Suffix | Role | Why it corrupts |
|---|---|---|
| `.__count__` | CArray element-count prefix (u32) | Bumping count without inserting matching element data desyncs the stream; every subsequent read consumes the wrong bytes. |
| `.__len__` | CString length prefix (u32) | Length without matching payload truncates or over-reads. |
| `.__tag__` | COptional presence tag (u8) | Flipping 0→1 without supplying inner-struct bytes makes the reader consume arbitrary following bytes. |
| *(no suffix — split primitive)* | Patch lands inside an existing primitive field | Writing 4 bytes at byte-offset 1 of a u32 splits the field; every later offset shifts. |

Plus:

- **`insert` patches** grow the file. Unless the mod author also ships a regenerated `pabgh`, the index is stale. Most mods with inserts are time bombs.
- **Absolute-offset `replace` patches** break the moment any other mod shifts bytes before that offset. Looking at you, Ultimate Lantern Reborn.

Implementation: `iteminfo_inspector.count_dangerous_patches(inspections)` flags every one of these and emits a warning to the user.

---

## 7. Reparse-Diff (RPD) — the safe ingest for byte-patch mods

Don't byte-apply a suspicious mod directly. Instead:

1. **Splice** all its patches into vanilla pabgb byte-for-byte (even inserts — just grow the buffer).
2. **Reparse** the result into a dict tree with `crimson_rs.parse_iteminfo_from_bytes`.
3. **Diff** against vanilla's parsed dict tree to extract field-level edits (`SET`, `APPEND`, `REMOVE_IDX`, `ADD_ENTRY`, `REMOVE_ENTRY`).
4. **Apply** those field edits to your merged dict, then serialize normally.

This recovers the mod author's *intent* without inheriting their offset assumptions. Real-world results:

| Mod | Byte-apply result | Reparse-Diff result |
|---|---|---|
| AccessorySocketsMod (411 patches) | 0 applied, 411 stale on user's vanilla | 184 clean field edits recovered |
| Ultimate Lantern Reborn v1.0 (2 patches, abs-offset + insert) | Crashes | 33 clean field edits (+7 passives, +25 buffs on 5 lanterns, adds FireFly_Lantern) |

API: `iteminfo_inspector.reparse_diff_patches(vanilla, doc, entry_blob_start=0)` → `ReparseDiffReport`. Apply the report's `edits` via `apply_field_edits(items_dict, report.edits)`.

---

## 8. Stacker Tool architecture

Reference implementation of all the rules above. Pipeline:

```
Sources (legacy JSON / folder PAZ / loose pabgb / ItemBuffs snapshot)
   ↓
Inspect patches — resolve byte offsets to field paths; flag danger signals
   ↓
Per-source mode: STR (strict byte-apply) | SEM (semantic field-apply) | RPD (reparse-diff)
   ↓
Dict-level merge across all sources (last writer wins on field conflicts)
   ↓
Bucket B: toggle-style bulk edits (max stacks, inf-durability)
   ↓
Serialize merged dict → iteminfo.pabgb
   ↓
Bucket C: post-serialize byte patches (cooldowns, transmog, VFX offsets)
   ↓
Regenerate iteminfo.pabgh from the merged bytes
   ↓
Bucket D: split sibling files — skill.* bundles with iteminfo, equipslotinfo.* separate
   ↓
Deploy:
   Apply Stack   → <game>/0062/ (iteminfo side) + <game>/0063/ (equipslotinfo)
   Export        → <export>/0036/ + <export>/0037/ + meta/0.papgt (drop-in)
```

### The bucket model

Sources contribute different types of edits that need different merge strategies:

- **Bucket A — dict entries**: parsed iteminfo fields. Merged field-by-field across all sources. Last writer wins.
- **Bucket B — toggle bulks**: max_stacks → 999999, inf_durability → 65535. Applied after dict merge, before serialize.
- **Bucket C — post-serialize byte patches**: cooldowns, transmog prefab hashes, VFX offsets. Applied to the serialized bytes because they target offsets that only exist post-serialize.
- **Bucket D — sibling files**: `skill.pabgb`/`skill.pabgh` (bundled with iteminfo), `equipslotinfo.pabgb`/`equipslotinfo.pabgh` (separate overlay group per split rule).

---

## 9. Checklist for tool authors

Before shipping an iteminfo tool/mod:

- [ ] Output `iteminfo.pabgb` is produced via dict-tree serialize, not byte-patched.
- [ ] A matching `iteminfo.pabgh` is regenerated from the output pabgb and shipped alongside.
- [ ] If editing `equipslotinfo.*`, it ships in a SEPARATE overlay group from iteminfo (split-overlay rule).
- [ ] Overlay group name is numeric (`0036`, `0062`, etc.), not `stk1` / `mymod`.
- [ ] `meta/0.papgt` has your group entries with correct `pack_meta_checksum` matching each `0.pamt` — use `crimson_rs.parse_pamt_bytes(pamt)["checksum"]` to compute.
- [ ] Your `0.paz` uses `INTERNAL_DIR = "gamedata/binary__/client/bin"` (short paths silently fail).
- [ ] If ingesting byte-patch mods, default to RPD mode; never byte-apply a mod flagged with `.__count__`, `.__len__`, `.__tag__`, or split-primitive patches.
- [ ] JSON offsets are parsed as hex-or-decimal (try `int(s, 16)` first, fall back to `int(s)`) — JMM-style writes hex without `0x` prefix.
- [ ] Apply errors are surfaced to the user, not silently swallowed. If `serialize_iteminfo` raises, abort deployment; don't fall back to a stale byte buffer.

---

## 10. Known pitfalls

### 10.1 Silent fallback to stale byte buffer

If `crimson_rs.serialize_iteminfo(items)` raises, don't silently fall back to the pre-mutation byte buffer — it drops every dict edit this session while claiming success to the user. Either raise with the exception message visible, or abort deployment. See `buffs_v319.py:_buff_apply_to_game` for the correct error-surfacing pattern.

### 10.2 Dev path is dead in retail

Overlays written to `gamedata/binary__/client/bin_dev/` (note the `_dev` suffix) are gated by a zero-locked flag in retail. Verified via IDA. Don't build "debug" or "dev" paths assuming they'll load — they silently do nothing.

### 10.3 PAZ internal path must be the full path

For `crimson_rs.PackGroupBuilder.add_file` and `crimson_rs.pack_mod`, the internal directory must be the FULL `gamedata/binary__/client/bin/` path, not just `gamedata/`. Short paths produce PAMT entries the game can't match against its file lookups, and the override silently fails.

### 10.4 Item additions need pabgh count update

If a mod adds new items (not just modifies existing ones), the pabgh count field (first u16) must reflect the new total. `build_iteminfo_pabgh` handles this automatically by deriving count from `len(items)` in the tracked-parse output. Don't hand-maintain counts.

### 10.5 Three separate ID namespaces — don't mix

Stats, Buffs, and Passive skills are three DIFFERENT id namespaces. Hash `91011` in the passive-skill table is a different object than hash `91011` in the buffs table. When cross-referencing, always track which namespace you're in.

### 10.6 Assume overlays conflict at file level, not field level

Two overlays shipping `iteminfo.pabgb` do NOT merge at the field level at runtime — the game loads one file and discards the other. Do your merging at build time (in your tool, dict-level) and emit a single overlay. This is the whole reason the Stacker tool exists.

### 10.7 Test in-game, not just by parse-round-trip

Tree verification (parse passes, serialize matches) is necessary but not sufficient. A save/mod can round-trip cleanly and still crash the game because the game reads a field we don't parse. Always verify the end-to-end path in-game before shipping.

### 10.8 Backup before writing

Users have real save files and real PAPGT registrations. Before writing ANY file the game reads, back up the prior state. `meta/0.papgt.sebak` and similar fallbacks belong at every overwrite site.

---

## 11. Reference implementation files

| File | Purpose |
|---|---|
| `crimson-rs` (Rust crate + Python bindings) | Parser/serializer for iteminfo, PAZ toolkit, PAPGT/PAMT readers and writers, tracked-parse (`parse_iteminfo_tracked`), `inspect_legacy_patches` for JSON-mod field resolution. |
| `CrimsonGameMods/item_creator.py` | `build_iteminfo_pabgh`, `append_items_to_iteminfo`, `add_item_to_store`, paloc/name-string patching helpers. |
| `CrimsonGameMods/gui/tabs/stacker.py` | Full merge pipeline, per-source STR/SEM/RPD toggles, Apply Stack (0062/0063 deploy), Folder Export (0036/0037 + meta/0.papgt drop-in). |
| `CrimsonGameMods/gui/tabs/iteminfo_inspector.py` | `collect_iteminfo_patches`, `inspect_patches`, `reparse_diff_patches`, `apply_field_edits`, `apply_semantic`, `count_dangerous_patches`, `format_danger_warnings`. |
| `CrimsonGameMods/gui/tabs/buffs_v319.py` | ItemBuffs tab: one-click feature bundle, UP v2, force-sockets, cooldowns, transmog. Reference for split-overlay deployment with 0058/0059. |
| `CrimsonGameMods/equipslotinfo_parser.py` | equipslotinfo.pabgb/pabgh parser + serializer used by UP v2's slot-expansion logic. |

---

## 12. Changelog of this reference

- **2026-04-21**: Initial version. Documents split-overlay rule, pabgh regeneration, numeric group names, RPD safe ingest, byte-patch corruption vectors. Built from empirical session debugging Kliff-musket unequippability + Ultimate Lantern Reborn stacking.
