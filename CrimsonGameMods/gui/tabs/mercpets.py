"""MercPets tab — editor for mercenaryinfo.pabgb.

Covers the three umbrella categories the game calls "mercenary":
    Mercenary  (NPC recruits)
    Pet        (pets / small companions)
    Vehicle    (mounts / rideables)
plus 8 subtype slots whose semantic we haven't confirmed yet.

Each of the 11 records holds 3 editable caps (active/owned/max) and 9
boolean flags. Edit → Stage → Apply to Game / Export as Mod.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QCheckBox, QGroupBox, QScrollArea, QMessageBox, QFrame, QSizePolicy,
    QApplication, QComboBox,
)

from gui.theme import COLORS

log = logging.getLogger(__name__)


CAP_PRESETS = [
    ("Vanilla", None),
    ("2× everything", 2),
    ("5× everything", 5),
    ("10× everything", 10),
    ("999 / 9999 / unlimited", 999),
]


class MercPetsTab(QWidget):
    config_save_requested = Signal()

    def __init__(self, config: dict, game_path_getter, parent=None):
        super().__init__(parent)
        self._config = config
        self._get_game_path = game_path_getter
        self._records = []  # mip.MercenaryRecord list
        self._vanilla_pabgh = b''
        self._vanilla_pabgb = b''
        self._modified = False
        self._row_widgets: list[dict] = []  # per-record widget bundle
        self._build_ui()

    def set_experimental_mode(self, enabled: bool) -> None:
        if hasattr(self, '_dev_export_btn'):
            self._dev_export_btn.setVisible(bool(enabled))

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        tutorial = QLabel(
            "<b>MercPets — edit pet / mercenary / vehicle caps</b><br>"
            "Each row is a summon category. <b>Active</b> = how many can be "
            "deployed at once. <b>Owned</b> = inventory cap. <b>Max</b> = absolute "
            "hard cap (-1 = unlimited). Flip toggles to change behaviors.<br>"
            "<b>Workflow:</b> 1. Load. 2. Edit sliders/toggles or pick a preset. "
            "3. Apply to Game or Export as Mod."
        )
        tutorial.setTextFormat(Qt.RichText)
        tutorial.setWordWrap(True)
        tutorial.setStyleSheet(
            f"color: {COLORS['text']}; background-color: {COLORS['panel']}; "
            f"padding: 8px; border: 2px solid {COLORS['accent']}; "
            f"border-radius: 6px;")
        layout.addWidget(tutorial)

        top_row = QHBoxLayout()
        load_btn = QPushButton("Load mercenaryinfo")
        load_btn.setObjectName("accentBtn")
        load_btn.clicked.connect(self._load)
        top_row.addWidget(load_btn)

        preset_label = QLabel("Quick Presets:")
        preset_label.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        top_row.addWidget(preset_label)

        for label, mult in CAP_PRESETS:
            b = QPushButton(label)
            b.setToolTip(f"Apply preset: {label}")
            b.clicked.connect(lambda _c=False, m=mult: self._apply_preset(m))
            top_row.addWidget(b)

        top_row.addStretch()

        apply_btn = QPushButton("Apply to Game")
        apply_btn.setStyleSheet("background-color: #B71C1C; color: white; font-weight: bold;")
        apply_btn.clicked.connect(self._apply_to_game)
        top_row.addWidget(apply_btn)

        export_btn = QPushButton("Export as Mod")
        export_btn.setStyleSheet("background-color: #7B1FA2; color: white; font-weight: bold;")
        export_btn.setToolTip("ADVANCED — UNSUPPORTED. Contact mod loader dev for help.")
        export_btn.clicked.connect(self._export_mod)
        export_btn.setVisible(False)
        top_row.addWidget(export_btn)
        self._dev_export_btn = export_btn

        restore_btn = QPushButton("Restore")
        restore_btn.setStyleSheet("background-color: #37474F; color: white; font-weight: bold;")
        restore_btn.clicked.connect(self._restore)
        top_row.addWidget(restore_btn)

        layout.addLayout(top_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._rows_host = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_host)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(4)
        self._rows_layout.addStretch(1)
        scroll.setWidget(self._rows_host)
        layout.addWidget(scroll, 1)

        self._status = QLabel("Click 'Load mercenaryinfo' to begin.")
        self._status.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 4px;")
        layout.addWidget(self._status)

    # ── actions ────────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            import mercenaryinfo_parser as mip
            import crimson_rs
        except Exception as e:
            QMessageBox.critical(self, "Load", f"Import failed:\n{e}")
            return
        gp = self._get_game_path()
        if not gp:
            QMessageBox.warning(self, "Load", "Set the game install path first.")
            return
        try:
            h = crimson_rs.extract_file(gp, '0008',
                'gamedata/binary__/client/bin', 'mercenaryinfo.pabgh')
            b = crimson_rs.extract_file(gp, '0008',
                'gamedata/binary__/client/bin', 'mercenaryinfo.pabgb')
        except Exception as e:
            QMessageBox.critical(self, "Load", f"Extract failed:\n{e}")
            return

        if not mip.roundtrip_test(h, b):
            QMessageBox.warning(self, "Load",
                "Parser roundtrip mismatch — aborting. File format may have changed.")
            return

        self._vanilla_pabgh = h
        self._vanilla_pabgb = b
        self._records = mip.parse_all(h, b)
        self._modified = False
        self._rebuild_rows()
        self._status.setText(
            f"Loaded {len(self._records)} records. Edit and click Apply / Export.")

    def _rebuild_rows(self) -> None:
        # Clear existing rows
        while self._rows_layout.count() > 1:
            item = self._rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._row_widgets.clear()

        import mercenaryinfo_parser as mip
        for rec in self._records:
            label = mip.category_label(rec.key)
            desc = mip.category_description(rec.key)
            box = QGroupBox(f"{label}  (slot {rec.key})")
            box.setToolTip(desc)
            box.setStyleSheet(
                "QGroupBox { font-weight: bold; border: 1px solid " + COLORS['border']
                + "; margin-top: 6px; padding-top: 10px; }")

            # Inline caption showing the descriptive guess text
            caption = QLabel(desc.replace('\n', ' '))
            caption.setStyleSheet(
                f"color: {COLORS['text_dim']}; font-size: 11px; "
                f"padding: 2px 6px;")
            caption.setWordWrap(True)
            vbox = QVBoxLayout(box)
            vbox.addWidget(caption)

            top = QHBoxLayout()
            def _spin(val: int, tip: str, minv: int = -1, maxv: int = 2_000_000_000):
                s = QSpinBox()
                s.setRange(minv, maxv)
                s.setValue(val)
                s.setToolTip(tip)
                s.setMinimumWidth(120)
                s.valueChanged.connect(self._mark_modified)
                return s

            summon_spin = _spin(rec.default_summon_count,
                "Active count — how many deployed at once (in field / summoned).")
            hire_spin = _spin(rec.default_hire_count,
                "Owned count — inventory cap. The number visible in-game (30 for pets).")
            max_spin = _spin(rec.max_hire_count,
                "Absolute hard cap. -1 means unlimited.")

            top.addWidget(QLabel("Active:"));  top.addWidget(summon_spin)
            top.addWidget(QLabel("Owned:"));   top.addWidget(hire_spin)
            top.addWidget(QLabel("Max:"));     top.addWidget(max_spin)
            top.addStretch()
            vbox.addLayout(top)

            # Boolean flags
            flag_row = QHBoxLayout()
            def _check(label: str, val: int, tip: str):
                c = QCheckBox(label)
                c.setChecked(bool(val))
                c.setToolTip(tip)
                c.stateChanged.connect(self._mark_modified)
                return c

            is_blocked_cb = _check("Blocked", rec.is_blocked,
                "Record disabled entirely. Leave off unless you know what you're doing.")
            is_ctrl_cb = _check("Controllable", rec.is_controllable,
                "Player can issue commands (follow/stay/attack).")
            new_main_cb = _check("New = Main", rec.set_new_mercenary_is_main,
                "Newly hired merc/pet becomes the 'main' one automatically.")
            per_tribe_cb = _check("Main-per-Tribe", rec.main_mercenary_per_tribe,
                "Only one 'main' per tribe allowed.")
            stack_cb = _check("Force Stackable", rec.is_force_stackable,
                "Duplicates stack into one inventory entry.")
            sell_cb = _check("Sellable", rec.is_sellable,
                "Can be sold at vendors.")
            camp_cb = _check("Use Camp Level", rec.use_camp_level,
                "Camp level gates available count.")
            apply_stat_cb = _check("Apply EquipStat", rec.apply_equip_item_stat,
                "Equipped item stats apply to this summon.")
            far_cb = _check("FarFromLeader opt", rec.far_from_leader_option,
                "Behavior when far from leader (recall, despawn, etc.)")
            spawn_cb = _check("SpawnPos Type", rec.spawn_position_type,
                "Spawn-position behavior flag.")

            for cb in (is_blocked_cb, is_ctrl_cb, new_main_cb, per_tribe_cb,
                       stack_cb, sell_cb, camp_cb, apply_stat_cb, far_cb, spawn_cb):
                flag_row.addWidget(cb)
            flag_row.addStretch()
            vbox.addLayout(flag_row)

            self._row_widgets.append({
                'rec': rec,
                'summon': summon_spin, 'hire': hire_spin, 'max': max_spin,
                'blocked': is_blocked_cb, 'ctrl': is_ctrl_cb,
                'new_main': new_main_cb, 'per_tribe': per_tribe_cb,
                'stack': stack_cb, 'sell': sell_cb,
                'camp': camp_cb, 'apply_stat': apply_stat_cb,
                'far': far_cb, 'spawn': spawn_cb,
            })

            self._rows_layout.insertWidget(self._rows_layout.count() - 1, box)

    def _mark_modified(self) -> None:
        self._modified = True
        self._status.setText("Modified — click Apply to Game or Export as Mod to write.")

    def _apply_preset(self, multiplier: int | None) -> None:
        if not self._records:
            QMessageBox.information(self, "Preset",
                "Load mercenaryinfo first.")
            return
        import mercenaryinfo_parser as mip
        if multiplier is None:
            # reset to vanilla
            self._records = mip.parse_all(self._vanilla_pabgh, self._vanilla_pabgb)
        else:
            # Multiply non-sentinel caps
            for rec in self._records:
                if rec.default_summon_count > 0 and rec.default_summon_count != -1:
                    rec.default_summon_count = min(
                        2_000_000_000,
                        rec.default_summon_count * multiplier
                        if multiplier != 999 else max(9999, rec.default_summon_count * 10))
                if rec.default_hire_count > 0 and rec.default_hire_count != -1:
                    rec.default_hire_count = min(
                        2_000_000_000,
                        rec.default_hire_count * multiplier
                        if multiplier != 999 else max(99999, rec.default_hire_count * 100))
                if multiplier == 999:
                    rec.max_hire_count = -1
        self._rebuild_rows()
        self._modified = True
        self._status.setText(
            f"Preset applied (x{multiplier if multiplier else 'vanilla'}). "
            f"Click Apply to Game.")

    def _collect_edits(self) -> None:
        for row in self._row_widgets:
            r = row['rec']
            r.default_summon_count = row['summon'].value()
            r.default_hire_count = row['hire'].value()
            r.max_hire_count = row['max'].value()
            r.is_blocked = int(row['blocked'].isChecked())
            r.is_controllable = int(row['ctrl'].isChecked())
            r.set_new_mercenary_is_main = int(row['new_main'].isChecked())
            r.main_mercenary_per_tribe = int(row['per_tribe'].isChecked())
            r.is_force_stackable = int(row['stack'].isChecked())
            r.is_sellable = int(row['sell'].isChecked())
            r.use_camp_level = int(row['camp'].isChecked())
            r.apply_equip_item_stat = int(row['apply_stat'].isChecked())
            r.far_from_leader_option = int(row['far'].isChecked())
            r.spawn_position_type = int(row['spawn'].isChecked())

    def _serialize(self) -> tuple[bytes, bytes]:
        import mercenaryinfo_parser as mip
        self._collect_edits()
        return mip.serialize_all(self._records)

    def _apply_to_game(self) -> None:
        if not self._records:
            QMessageBox.information(self, "Apply", "Load first.")
            return
        import crimson_rs

        gp = self._get_game_path()
        if not gp:
            QMessageBox.warning(self, "Apply", "Set the game install path first.")
            return

        new_h, new_b = self._serialize()
        INTERNAL_DIR = "gamedata/binary__/client/bin"
        overlay_group = "0063"

        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                group_dir = os.path.join(tmp_dir, overlay_group)
                builder = crimson_rs.PackGroupBuilder(
                    group_dir, crimson_rs.Compression.NONE, crimson_rs.Crypto.NONE)
                builder.add_file(INTERNAL_DIR, "mercenaryinfo.pabgb", new_b)
                builder.add_file(INTERNAL_DIR, "mercenaryinfo.pabgh", new_h)
                pamt_bytes = bytes(builder.finish())
                pamt_checksum = crimson_rs.parse_pamt_bytes(pamt_bytes)["checksum"]

                game_overlay = os.path.join(gp, overlay_group)
                os.makedirs(game_overlay, exist_ok=True)
                for f in os.listdir(group_dir):
                    shutil.copy2(os.path.join(group_dir, f),
                                 os.path.join(game_overlay, f))

                papgt_path = os.path.join(gp, "meta", "0.papgt")
                bak = papgt_path + ".mercpets_bak"
                if not os.path.exists(bak) and os.path.isfile(papgt_path):
                    shutil.copy2(papgt_path, bak)

                cur = crimson_rs.parse_papgt_file(papgt_path) \
                    if os.path.isfile(papgt_path) else {"entries": []}
                cur["entries"] = [e for e in cur["entries"]
                                  if e.get("group_name") != overlay_group]
                cur = crimson_rs.add_papgt_entry(
                    cur, overlay_group, pamt_checksum,
                    is_optional=0, language=0x3FFF)
                crimson_rs.write_papgt_file(cur, papgt_path)

            self._modified = False
            self._status.setText(
                f"Applied to {overlay_group}/ — restart the game to see the new caps.")
            QMessageBox.information(self, "Applied",
                f"Overlay deployed at {overlay_group}/.\n"
                f"Restart Crimson Desert to see the new pet/merc/vehicle caps.\n"
                f"Click Restore to undo.")
        except PermissionError:
            QMessageBox.critical(self, "Apply",
                "Permission denied. Run the editor as Administrator.")
        except Exception as e:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, "Apply", f"Failed:\n{e}")

    def _export_mod(self) -> None:
        if not self._records:
            QMessageBox.information(self, "Export", "Load first.")
            return
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Export Mod",
            "Mod name:", text="MercPets Custom Caps")
        if not ok or not name.strip():
            return
        name = name.strip()
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        folder = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
        out = os.path.join(exe_dir, "packs", folder)
        os.makedirs(out, exist_ok=True)
        files_dir = os.path.join(out, "files", "gamedata", "binary__", "client", "bin")
        os.makedirs(files_dir, exist_ok=True)
        new_h, new_b = self._serialize()
        with open(os.path.join(files_dir, "mercenaryinfo.pabgb"), "wb") as f:
            f.write(new_b)
        with open(os.path.join(files_dir, "mercenaryinfo.pabgh"), "wb") as f:
            f.write(new_h)
        import json
        with open(os.path.join(out, "modinfo.json"), "w", encoding="utf-8") as f:
            json.dump({
                "id": name.lower().replace(" ", "_"),
                "name": name,
                "version": "1.0.0",
                "game_version": "1.00.03",
                "author": "CrimsonSaveEditor",
                "description": f"MercPets mod: {name}",
            }, f, indent=2)
        self._status.setText(f"Exported mod to packs/{folder}/")
        QMessageBox.information(self, "Exported",
            f"Mod written to:\n{out}")

    def _restore(self) -> None:
        gp = self._get_game_path()
        if not gp:
            return
        overlay_group = "0063"
        overlay = os.path.join(gp, overlay_group)
        papgt = os.path.join(gp, "meta", "0.papgt")
        bak = papgt + ".mercpets_bak"
        try:
            if os.path.isdir(overlay):
                shutil.rmtree(overlay)
            if os.path.exists(bak):
                shutil.copy2(bak, papgt)
            self._status.setText(f"Restored — {overlay_group}/ overlay removed.")
            QMessageBox.information(self, "Restored",
                "MercPets overlay removed. Restart game to confirm.")
        except Exception as e:
            QMessageBox.critical(self, "Restore", f"Failed:\n{e}")
