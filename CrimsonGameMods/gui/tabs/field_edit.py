from __future__ import annotations

import json
import logging
import os
import shutil
import struct
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui.theme import COLORS
from gui.utils import make_scope_label
from i18n import tr

log = logging.getLogger(__name__)


class FieldEditTab(QWidget):

    status_message = Signal(str)
    config_save_requested = Signal()

    def __init__(
        self,
        config: dict,
        rebuild_papgt_fn: Optional[Callable[[str, str], str]] = None,
        show_guide_fn=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._rebuild_papgt_fn = rebuild_papgt_fn
        self._show_guide_fn = show_guide_fn

        self._field_edit_data = None
        self._field_edit_original = None
        self._field_edit_schema = None
        self._field_edit_entries: list = []
        self._field_edit_modified = False
        self._field_edit_editing = False
        self._vehicle_data = None
        self._vehicle_original = None
        self._vehicle_schema = None
        self._vehicle_entries: list = []
        self._vehicle_editing = False
        self._gptrigger_data = None
        self._gptrigger_original = None
        self._gptrigger_schema = None
        self._gptrigger_entries: list = []
        self._gptrigger_editing = False
        self._regioninfo_data = None
        self._regioninfo_original = None
        self._regioninfo_schema = None
        self._regioninfo_entries: list = []
        self._regioninfo_editing = False
        self._charinfo_data = None
        self._charinfo_original = None
        self._charinfo_schema = None
        self._charinfo_mount_entries: list = []
        self._charinfo_editing = False
        self._wantedinfo_data = None
        self._wantedinfo_original = None
        self._wantedinfo_schema = None
        self._mesh_swap_queue: list = []
        self._allygroup_data = None
        self._allygroup_original = None
        self._allygroup_schema = None
        self._relationinfo_data = None
        self._relationinfo_original = None
        self._relationinfo_schema = None
        self._factionrelgrp_data = None
        self._factionrelgrp_original = None
        self._factionrelgrp_schema = None

        self._build_ui()


    def set_game_path(self, path: str) -> None:
        if path:
            self._config["game_install_path"] = path

    def set_experimental_mode(self, enabled: bool) -> None:
        for w in getattr(self, '_dev_export_btns_field', []):
            w.setVisible(bool(enabled))

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)

        layout = QVBoxLayout(inner)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(make_scope_label("game"))

        info = QLabel(
            "Edit zone/field properties — enable mount summoning in towns, "
            "modify zone flags. Modifies fieldinfo / vehicleinfo / regioninfo / "
            "characterinfo / wantedinfo via a single PAZ overlay (0039/)."
        )
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color: {COLORS['accent']}; padding: 8px; "
            f"border: 1px solid {COLORS['border']}; border-radius: 4px; "
            f"background-color: rgba(218,168,80,0.08);"
        )
        layout.addWidget(info)

        top_row = QHBoxLayout()

        load_btn = QPushButton(tr("Load FieldInfo"))
        load_btn.setObjectName("accentBtn")
        load_btn.setToolTip(tr("Extract fieldinfo.pabgb from game PAZ"))
        load_btn.clicked.connect(self._field_edit_load)
        top_row.addWidget(load_btn)

        mesh_swap_btn = QPushButton(tr("🎭 Mesh Swap (Pets / Mounts / NPCs)"))
        mesh_swap_btn.setStyleSheet(
            "background-color: #4A148C; color: white; font-weight: bold; "
            "padding: 8px 14px; font-size: 13px;")
        mesh_swap_btn.setToolTip(
            "Visual Transmog for ANY character in the game.\n\n"
            "• Make your pet cat look like a wolf\n"
            "• Make your horse look like a dragon\n"
            "• Make NPCs look like bosses\n"
            "• Make bosses look like chickens\n\n"
            "Opens a dedicated dialog with Pet / Animal / Mount / Hero / Boss\n"
            "categories so you can find what to swap without needing to know\n"
            "internal names. Target and Source filters are independent — filter\n"
            "your target to Pet (Cat) on the left, then filter source to Dragon\n"
            "on the right to make all cats look like wyverns.\n\n"
            "Queued into the 0039/ overlay — apply via Apply to Game or Export.")
        mesh_swap_btn.clicked.connect(self._field_edit_open_mesh_swap)
        top_row.addWidget(mesh_swap_btn)

        mount_btn = QPushButton(tr("Enable Mounts Everywhere"))
        mount_btn.setStyleSheet("background-color: #7B1FA2; color: white; font-weight: bold;")
        mount_btn.setToolTip(
            "Patches 3 game files to allow mounts everywhere:\n"
            "1. vehicleinfo: allow all mounts in safe zones\n"
            "2. regioninfo: remove dismount flags from towns / restricted areas\n"
            "3. characterinfo: extend ride duration, remove cooldowns")
        mount_btn.clicked.connect(self._field_edit_enable_mounts)
        top_row.addWidget(mount_btn)

        killall_btn = QPushButton(tr("Make All NPCs Killable"))
        killall_btn.setStyleSheet("background-color: #B71C1C; color: white; font-weight: bold;")
        killall_btn.setToolTip(
            "Sets _isAttackable=1 and _invincibility=0 on all NPCs.\n\n"
            "Killing quest-essential NPCs may affect quest progression.\n"
            "Fully reversible via Restore.\n\n"
            "IMPORTANT: After a game update, click Restore first to remove\n"
            "the old overlay, then Load FieldInfo + re-apply.")
        killall_btn.clicked.connect(self._field_edit_make_killable)
        top_row.addWidget(killall_btn)

        mount_inv_btn = QPushButton(tr("Invincible Mounts"))
        mount_inv_btn.setStyleSheet("background-color: #1565C0; color: white; font-weight: bold;")
        mount_inv_btn.setToolTip(
            "Sets _invincibility=1 on all mounts/vehicles.\n"
            "Mounts can no longer be killed by enemies.\n"
            "Fully reversible via Restore.")
        mount_inv_btn.clicked.connect(self._field_edit_invincible_mounts)
        top_row.addWidget(mount_inv_btn)

        apply_btn = QPushButton(tr("Apply to Game"))
        apply_btn.setObjectName("accentBtn")
        apply_btn.setToolTip(tr("Deploy modified fieldinfo to the game"))
        apply_btn.clicked.connect(self._field_edit_apply)
        top_row.addWidget(apply_btn)

        # Export buttons — only visible in Advanced/Dev mode (unsupported)
        export_mod_btn = QPushButton(tr("Export as Mod"))
        export_mod_btn.setStyleSheet("background-color: #2E7D32; color: white; font-weight: bold;")
        export_mod_btn.setToolTip(
            "ADVANCED — UNSUPPORTED. Contact mod loader dev for help.\n\n"
            "Export as raw-pabgb mod for generic mod loaders.")
        export_mod_btn.clicked.connect(self._field_edit_export_mod)
        export_mod_btn.setVisible(False)
        top_row.addWidget(export_mod_btn)

        export_btn = QPushButton(tr("Export as CDUMM Mod"))
        export_btn.setStyleSheet("background-color: #1B5E20; color: white; font-weight: bold;")
        export_btn.setToolTip(
            "ADVANCED — UNSUPPORTED. Contact mod loader dev for help.\n\n"
            "Export as pre-packed PAZ mod for JMM / CDUMM / DMM.")
        export_btn.clicked.connect(self._field_edit_export)
        export_btn.setVisible(False)
        top_row.addWidget(export_btn)

        export_json_btn = QPushButton(tr("Export as JSON"))
        export_json_btn.setStyleSheet("background-color: #0D47A1; color: white; font-weight: bold;")
        export_json_btn.setToolTip(
            "ADVANCED — UNSUPPORTED. Contact mod loader dev for help.\n\n"
            "Export all changes as a portable JSON patch file.")
        export_json_btn.clicked.connect(self._field_edit_export_json)
        export_json_btn.setVisible(False)
        top_row.addWidget(export_json_btn)

        self._dev_export_btns_field = [export_mod_btn, export_btn, export_json_btn]

        # Export Mesh Swap as JSON Mod — removed. Redundant with Export as
        # JSON which already bakes mesh swaps via _apply_mesh_swaps() before
        # diffing. Apply to Game / Export as Mod / Export as CDUMM also
        # include mesh swaps. Keep the method around for backward-compat in
        # case anything still references it, but don't surface a button.

        restore_btn = QPushButton(tr("Restore"))
        restore_btn.setToolTip(tr("Remove field mod and restore vanilla"))
        restore_btn.clicked.connect(self._field_edit_restore)
        top_row.addWidget(restore_btn)

        self._field_edit_status = QLabel("")
        top_row.addWidget(self._field_edit_status, 1)
        layout.addLayout(top_row)

        ally_row = QHBoxLayout()
        ally_row.setSpacing(6)
        ally_label = QLabel(tr("Attack Anything (EXPERIMENTAL):"))
        ally_label.setStyleSheet("color: #FFB74D; font-weight: bold;")
        ally_row.addWidget(ally_label)

        wipe_btn = QPushButton(tr("Wipe Ally Lists (Path B)"))
        wipe_btn.setStyleSheet("background-color: #AD1457; color: white; font-weight: bold;")
        wipe_btn.setToolTip(
            "Path B — PROBABLE. Zeros _addOnAllyGroupList hashes across all\n"
            "50 AllyGroup entries. Effect: no group is allied with any other.\n"
            "Less nuclear than Path A; _relationTypeList preserved so combat\n"
            "rules still apply, just no mutual defense between allied factions.")
        wipe_btn.clicked.connect(self._field_edit_wipe_ally_lists)
        ally_row.addWidget(wipe_btn)

        intruder2_btn = QPushButton(tr("Intruder Flag (slot 2)"))
        intruder2_btn.setStyleSheet("background-color: #4A148C; color: white; font-weight: bold;")
        intruder2_btn.setToolTip(
            "Path C — EXPERIMENT. Sets u8 flag slot #2 = 1 on all 50 groups.\n"
            "Likely _isIntruder (flag distribution suggests it). Test in-game\n"
            "and tell me if NPCs attack each other — if yes, this is _isIntruder.")
        intruder2_btn.clicked.connect(lambda: self._field_edit_set_ally_flag(2))
        ally_row.addWidget(intruder2_btn)


        ally_row.addStretch()
        layout.addLayout(ally_row)

        self._field_edit_table = QTableWidget()
        self._field_edit_table.setColumnCount(6)
        self._field_edit_table.setHorizontalHeaderLabels([
            "Key", "Name", "Zone Type", "canCallVehicle",
            "alwaysCallVehicle_dev", "Position",
        ])
        self._field_edit_table.horizontalHeader().setStretchLastSection(True)
        self._field_edit_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._field_edit_table.setAlternatingRowColors(True)
        self._field_edit_table.verticalHeader().setVisible(False)
        self._field_edit_table.cellChanged.connect(self._field_edit_cell_changed)
        self._field_edit_table.setVisible(False)

        vlabel = QLabel(tr("Vehicle Info (vehicleinfo.pabgb) — per-mount call restrictions:"))
        vlabel.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold; padding: 4px 0;")
        layout.addWidget(vlabel)

        self._vehicle_table = QTableWidget()
        self._vehicle_table.setColumnCount(7)
        self._vehicle_table.setHorizontalHeaderLabels([
            "Key", "Name", "Type", "VoxelType", "MountCallType",
            "CanCallSafeZone", "AltitudeCap",
        ])
        self._vehicle_table.horizontalHeader().setStretchLastSection(True)
        self._vehicle_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._vehicle_table.setAlternatingRowColors(True)
        self._vehicle_table.verticalHeader().setVisible(False)
        self._vehicle_table.cellChanged.connect(self._vehicle_cell_changed)
        layout.addWidget(self._vehicle_table, 1)

        self._gt_filter_combo = QComboBox()
        self._gt_filter_combo.addItem("Safe zones only (type != 0)", "safe")
        self._gt_filter_combo.addItem("All entries", "all")
        self._gt_table = QTableWidget()
        self._gt_table.setColumnCount(4)
        self._gt_table.setHorizontalHeaderLabels(["Key", "Name", "Flags", "safeZoneType"])
        self._gt_table.setVisible(False)

        rlabel = QLabel(tr("Region Info (regioninfo.pabgb) — town/dismount flags per region:"))
        rlabel.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold; padding: 4px 0;")
        layout.addWidget(rlabel)

        ri_filter_row = QHBoxLayout()
        ri_filter_row.addWidget(QLabel(tr("Show:")))
        self._ri_filter_combo = QComboBox()
        self._ri_filter_combo.addItem("Towns & restricted only", "restricted")
        self._ri_filter_combo.addItem("All regions", "all")
        self._ri_filter_combo.setFixedWidth(200)
        self._ri_filter_combo.currentIndexChanged.connect(self._regioninfo_populate)
        ri_filter_row.addWidget(self._ri_filter_combo)
        ri_filter_row.addStretch()
        layout.addLayout(ri_filter_row)

        self._ri_table = QTableWidget()
        self._ri_table.setColumnCount(7)
        self._ri_table.setHorizontalHeaderLabels([
            "Key", "Name", "Type", "isTown",
            "limitVehicleRun", "isWild", "vehicleMercAllowType",
        ])
        self._ri_table.horizontalHeader().setStretchLastSection(True)
        self._ri_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._ri_table.setAlternatingRowColors(True)
        self._ri_table.verticalHeader().setVisible(False)
        self._ri_table.cellChanged.connect(self._ri_cell_changed)
        layout.addWidget(self._ri_table, 1)

        mlabel = QLabel(tr("Mount Duration/Cooldown (characterinfo.pabgb) — per-mount ride limits:"))
        mlabel.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold; padding: 4px 0;")
        layout.addWidget(mlabel)

        self._mount_table = QTableWidget()
        self._mount_table.setColumnCount(5)
        self._mount_table.setHorizontalHeaderLabels([
            "Name", "Vehicle Type", "Duration (s)", "Cooldown (s)", "CoolType",
        ])
        self._mount_table.horizontalHeader().setStretchLastSection(True)
        self._mount_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._mount_table.setAlternatingRowColors(True)
        self._mount_table.verticalHeader().setVisible(False)
        self._mount_table.cellChanged.connect(self._mount_cell_changed)
        layout.addWidget(self._mount_table, 1)

        credit = QLabel(
            "FieldInfo: sub_1410403F0 | VehicleInfo: sub_14105D470 | "
            "Triggers: sub_141044180 | RegionInfo: sub_141053790 | "
            "CharacterInfo: sub_141037900")
        credit.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 2px;")
        layout.addWidget(credit)


    def _check_stale_field_overlay(self, game_path: str) -> None:
        mod_group = "0039"
        overlay_dir = os.path.join(game_path, mod_group)
        if not os.path.isdir(overlay_dir):
            return

        try:
            import crimson_rs
            dp = "gamedata/binary__/client/bin"
            vanilla_ci = crimson_rs.extract_file(game_path, "0008", dp, "characterinfo.pabgb")
            vanilla_size = len(vanilla_ci)

            pamt_path = os.path.join(overlay_dir, "0.pamt")
            if not os.path.isfile(pamt_path):
                return

            pamt_data = open(pamt_path, "rb").read()
            pamt = crimson_rs.parse_pamt_bytes(pamt_data)

            overlay_size = None
            for d in pamt.get("directories", []):
                for f in d.get("files", []):
                    if f.get("name", "") == "characterinfo.pabgb":
                        overlay_size = f.get("uncompressed_size", 0)
                        break
                if overlay_size is not None:
                    break

            if overlay_size is None:
                return

            if overlay_size != vanilla_size:
                reply = QMessageBox.warning(
                    self, tr("Stale Mod Detected"),
                    f"A FieldEdit overlay ({mod_group}/) was created for a different\n"
                    f"game version (file size mismatch: overlay={overlay_size:,}B vs "
                    f"current={vanilla_size:,}B).\n\n"
                    f"This is likely why the game won't start.\n"
                    f"Remove the stale overlay now?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if reply == QMessageBox.Yes:
                    self._field_edit_restore()
        except Exception as e:
            log.warning("Stale overlay check failed: %s", e)


    def _field_edit_load(self):
        game_path = self._config.get("game_install_path", "")
        if not game_path or not os.path.isdir(game_path):
            try:
                from crimson_rs.validate_game_dir import auto_detect_game_dir
                detected = auto_detect_game_dir()
                if detected and os.path.isdir(detected):
                    self._config["game_install_path"] = game_path = detected
                    self.config_save_requested.emit()
            except Exception:
                pass
        if not game_path or not os.path.isdir(game_path):
            QMessageBox.warning(self, tr("Game Path"),
                tr("Set the game install path first (top of the window)."))
            return

        self._check_stale_field_overlay(game_path)

        self._field_edit_status.setText(tr("Extracting fieldinfo..."))
        QApplication.processEvents()

        try:
            import crimson_rs
            dp = "gamedata/binary__/client/bin"
            body = crimson_rs.extract_file(game_path, "0008", dp, "fieldinfo.pabgb")
            schema = crimson_rs.extract_file(game_path, "0008", dp, "fieldinfo.pabgh")

            self._field_edit_data = bytearray(body)
            self._field_edit_original = bytes(body)
            self._field_edit_schema = bytes(schema)
            self._field_edit_modified = False

            from fieldinfo_parser import parse_pabgh_index, parse_entry
            idx = parse_pabgh_index(self._field_edit_schema)
            sorted_offs = sorted(set(idx.values()))
            entries = []
            for key, eoff in sorted(idx.items()):
                bi = sorted_offs.index(eoff)
                end = sorted_offs[bi + 1] if bi + 1 < len(sorted_offs) else len(self._field_edit_data)
                entry = parse_entry(bytes(self._field_edit_data), eoff, end)
                if entry:
                    entries.append(entry)

            self._field_edit_entries = entries
            self._field_edit_populate()

            try:
                vbody = crimson_rs.extract_file(game_path, "0008", dp, "vehicleinfo.pabgb")
                vschema = crimson_rs.extract_file(game_path, "0008", dp, "vehicleinfo.pabgh")
                self._vehicle_data = bytearray(vbody)
                self._vehicle_original = bytes(vbody)
                self._vehicle_schema = bytes(vschema)

                from vehicleinfo_parser import parse_pabgh_index_u16, parse_entry as vparse
                vidx = parse_pabgh_index_u16(self._vehicle_schema)
                vsorted = sorted(set(vidx.values()))
                ventries = []
                for vk, vo in sorted(vidx.items()):
                    vbi = vsorted.index(vo)
                    vend = vsorted[vbi + 1] if vbi + 1 < len(vsorted) else len(self._vehicle_data)
                    ve = vparse(bytes(self._vehicle_data), vo, vend)
                    if ve:
                        ventries.append(ve)
                self._vehicle_entries = ventries
                self._vehicle_populate()
            except Exception as _ve:
                log.exception("Could not load vehicleinfo")

            try:
                gt_body = crimson_rs.extract_file(game_path, "0008", dp, "gameplaytrigger.pabgb")
                gt_gh = crimson_rs.extract_file(game_path, "0008", dp, "gameplaytrigger.pabgh")
                self._gptrigger_data = bytearray(gt_body)
                self._gptrigger_original = bytes(gt_body)
                self._gptrigger_schema = bytes(gt_gh)

                import struct as _st
                _gt_G = self._gptrigger_schema
                _gt_c16 = _st.unpack_from('<H', _gt_G, 0)[0]
                if 2 + _gt_c16 * 8 == len(_gt_G):
                    _gt_idx_start, _gt_count = 2, _gt_c16
                else:
                    _gt_count = _st.unpack_from('<I', _gt_G, 0)[0]
                    _gt_idx_start = 4
                _gt_idx = {}
                for _gi in range(_gt_count):
                    _gp = _gt_idx_start + _gi * 8
                    if _gp + 8 > len(_gt_G): break
                    _gt_idx[_st.unpack_from('<I', _gt_G, _gp)[0]] = _st.unpack_from('<I', _gt_G, _gp + 4)[0]

                gt_entries = []
                for _gk, _go in sorted(_gt_idx.items()):
                    try:
                        p = _go
                        _rk = _st.unpack_from('<I', self._gptrigger_data, p)[0]; p += 4
                        _sl = _st.unpack_from('<I', self._gptrigger_data, p)[0]; p += 4
                        if _sl > 200: continue
                        _nm = self._gptrigger_data[p:p+_sl].decode('utf-8', errors='replace'); p += _sl
                        _f1 = self._gptrigger_data[p]; p += 1
                        _f2 = self._gptrigger_data[p]; p += 1
                        _f3 = self._gptrigger_data[p]; p += 1
                        _szt = self._gptrigger_data[p]
                        gt_entries.append({
                            'key': _gk, 'name': _nm,
                            'flag1': _f1, 'flag2': _f2, 'flag3': _f3,
                            'safe_zone_type': _szt,
                            'safe_zone_type_offset': p,
                        })
                    except Exception:
                        pass

                self._gptrigger_entries = gt_entries
                self._gptrigger_populate()
                log.info("Loaded %d gameplaytrigger entries", len(gt_entries))
            except Exception:
                log.exception("Could not load gameplaytrigger")
                self._gptrigger_entries = []

            try:
                ri_body = crimson_rs.extract_file(game_path, "0008", dp, "regioninfo.pabgb")
                ri_gh = crimson_rs.extract_file(game_path, "0008", dp, "regioninfo.pabgh")
                self._regioninfo_data = bytearray(ri_body)
                self._regioninfo_original = bytes(ri_body)
                self._regioninfo_schema = bytes(ri_gh)

                from regioninfo_parser import parse_pabgh_index as ri_idx, parse_region_entry
                idx_ri = ri_idx(self._regioninfo_schema)
                sorted_ri = sorted(idx_ri.items(), key=lambda x: x[1])
                ri_entries = []
                for i_ri, (rk, ro) in enumerate(sorted_ri):
                    rend = sorted_ri[i_ri + 1][1] if i_ri + 1 < len(sorted_ri) else len(self._regioninfo_data)
                    re_ = parse_region_entry(bytes(self._regioninfo_data), ro, rend)
                    if re_ and '_error' not in re_:
                        re_['_abs_offset'] = ro
                        ri_entries.append(re_)
                self._regioninfo_entries = ri_entries
                self._regioninfo_populate()
                log.info("Loaded %d regioninfo entries", len(ri_entries))
            except Exception:
                log.exception("Could not load regioninfo")
                self._regioninfo_entries = []

            try:
                ci_body = crimson_rs.extract_file(game_path, "0008", dp, "characterinfo.pabgb")
                ci_gh = crimson_rs.extract_file(game_path, "0008", dp, "characterinfo.pabgh")
                self._charinfo_data = bytearray(ci_body)
                self._charinfo_original = bytes(ci_body)
                self._charinfo_schema = bytes(ci_gh)

                from characterinfo_full_parser import parse_all_entries as ci_parse_all
                all_ci = ci_parse_all(bytes(self._charinfo_data), self._charinfo_schema)
                mount_entries = [e for e in all_ci
                                 if e.get('_vehicleInfo', 0) != 0
                                 or e.get('name', '').startswith('Riding_')]
                self._charinfo_mount_entries = mount_entries
                self._mount_populate()
                log.info("Loaded %d mount entries from characterinfo", len(mount_entries))
            except Exception:
                log.exception("Could not load characterinfo mounts")
                self._charinfo_mount_entries = []

            try:
                wi_body = crimson_rs.extract_file(game_path, "0008", dp, "wantedinfo.pabgb")
                wi_gh = crimson_rs.extract_file(game_path, "0008", dp, "wantedinfo.pabgh")
                self._wantedinfo_data = bytearray(wi_body)
                self._wantedinfo_original = bytes(wi_body)
                self._wantedinfo_schema = bytes(wi_gh)
                log.info("Loaded wantedinfo: %d bytes", len(wi_body))
            except Exception as _wie:
                self._wantedinfo_data = None
                log.warning("Could not load wantedinfo: %s", _wie)

            total_gt = sum(1 for e in self._gptrigger_entries if e['safe_zone_type'] != 0)
            ri_towns = sum(1 for e in self._regioninfo_entries if e.get('_isTown', 0))
            self._field_edit_status.setText(
                f"Loaded {len(entries)} zones + {len(self._vehicle_entries)} vehicles + "
                f"{len(self._gptrigger_entries)} triggers ({total_gt} safe zones) + "
                f"{len(self._regioninfo_entries)} regions ({ri_towns} towns) + "
                f"{len(self._charinfo_mount_entries)} mounts")

        except Exception as e:
            log.exception("FieldEdit load failed")
            self._field_edit_status.setText(f"Error: {e}")
            QMessageBox.critical(self, tr("Extract Failed"), str(e))


    def _field_edit_populate(self):
        self._field_edit_editing = True
        entries = self._field_edit_entries
        self._field_edit_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            item = QTableWidgetItem(str(e['key']))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._field_edit_table.setItem(row, 0, item)
            name = e.get('name', '') or f"Zone_{e['key']}"
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._field_edit_table.setItem(row, 1, item)
            item = QTableWidgetItem(str(e.get('zone_type', '?')))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._field_edit_table.setItem(row, 2, item)
            ccv = e.get('can_call_vehicle', 0)
            item = QTableWidgetItem(str(ccv))
            orig_ccv = self._field_edit_original[e['can_call_vehicle_offset']]
            if ccv != orig_ccv:
                item.setBackground(QColor(60, 40, 20))
            self._field_edit_table.setItem(row, 3, item)
            acv = e.get('always_call_vehicle_dev', 0)
            item = QTableWidgetItem(str(acv))
            orig_acv = self._field_edit_original[e['always_call_vehicle_dev_offset']]
            if acv != orig_acv:
                item.setBackground(QColor(80, 20, 60))
            if acv:
                item.setForeground(QColor(100, 255, 100))
            self._field_edit_table.setItem(row, 4, item)
            pos = e.get('position', (0, 0, 0))
            item = QTableWidgetItem(f"({pos[0]}, {pos[1]}, {pos[2]})")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._field_edit_table.setItem(row, 5, item)
        self._field_edit_table.resizeColumnsToContents()
        self._field_edit_editing = False

    def _field_edit_cell_changed(self, row, col):
        if self._field_edit_editing or not self._field_edit_entries:
            return
        if col not in (3, 4):
            return
        if row >= len(self._field_edit_entries):
            return
        cell = self._field_edit_table.item(row, col)
        if not cell:
            return
        try:
            new_val = int(cell.text())
        except ValueError:
            return
        new_val = max(0, min(new_val, 1))
        e = self._field_edit_entries[row]
        if col == 3:
            off = e['can_call_vehicle_offset']
            self._field_edit_data[off] = new_val
            e['can_call_vehicle'] = new_val
        elif col == 4:
            off = e['always_call_vehicle_dev_offset']
            self._field_edit_data[off] = new_val
            e['always_call_vehicle_dev'] = new_val
        self._field_edit_modified = True
        self._field_edit_status.setText(f"Modified zone {e['key']}")

    def _vehicle_populate(self):
        self._vehicle_editing = True
        entries = self._vehicle_entries
        self._vehicle_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            item = QTableWidgetItem(str(e['key']))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._vehicle_table.setItem(row, 0, item)
            item = QTableWidgetItem(e['name'])
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._vehicle_table.setItem(row, 1, item)
            item = QTableWidgetItem(str(e['vehicle_type']))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._vehicle_table.setItem(row, 2, item)
            vt = e['voxel_type']
            item = QTableWidgetItem(f"{vt} ({'fly' if vt == 7 else 'ground'})")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._vehicle_table.setItem(row, 3, item)
            mct = e['mount_call_type']
            item = QTableWidgetItem(str(mct))
            orig_mct = self._vehicle_original[e['mount_call_type_offset']]
            if mct != orig_mct:
                item.setBackground(QColor(60, 40, 20))
            item.setToolTip("0=siege/util, 1=rideable, 2=flying")
            self._vehicle_table.setItem(row, 4, item)
            ccsz = e['can_call_safe_zone']
            item = QTableWidgetItem(str(ccsz))
            orig_ccsz = self._vehicle_original[e['can_call_safe_zone_offset']]
            if ccsz != orig_ccsz:
                item.setBackground(QColor(80, 20, 60))
            if ccsz:
                item.setForeground(QColor(100, 255, 100))
            item.setToolTip("0=restricted, 1=can call in safe zones (towns)")
            self._vehicle_table.setItem(row, 5, item)
            alt = e['altitude_cap']
            if alt > 1e30:
                item = QTableWidgetItem("999999")
            else:
                item = QTableWidgetItem(f"{alt:.0f}")
            orig_alt = struct.unpack_from('<f', self._vehicle_original, e['altitude_cap_offset'])[0]
            if abs(alt - orig_alt) > 0.1:
                item.setBackground(QColor(60, 40, 20))
            item.setToolTip("Max flight altitude. Dragon=1350, rest=999999 (no cap)")
            self._vehicle_table.setItem(row, 6, item)
        self._vehicle_table.resizeColumnsToContents()
        self._vehicle_editing = False

    def _vehicle_cell_changed(self, row, col):
        if self._vehicle_editing or not self._vehicle_entries:
            return
        if col not in (4, 5, 6):
            return
        if row >= len(self._vehicle_entries):
            return
        cell = self._vehicle_table.item(row, col)
        if not cell:
            return
        e = self._vehicle_entries[row]
        if col == 6:
            try:
                new_val = float(cell.text())
            except ValueError:
                return
            new_val = max(0, new_val)
            struct.pack_into('<f', self._vehicle_data, e['altitude_cap_offset'], new_val)
            e['altitude_cap'] = new_val
            self._field_edit_modified = True
            self._field_edit_status.setText(f"Set {e['name']} altitude cap to {new_val:.0f}")
            return
        try:
            new_val = int(cell.text())
        except ValueError:
            return
        new_val = max(0, min(new_val, 255))
        if col == 4:
            self._vehicle_data[e['mount_call_type_offset']] = new_val
            e['mount_call_type'] = new_val
        elif col == 5:
            self._vehicle_data[e['can_call_safe_zone_offset']] = new_val
            e['can_call_safe_zone'] = new_val
        self._field_edit_modified = True
        self._field_edit_status.setText(f"Modified vehicle {e['name']}")

    def _gptrigger_populate(self):
        self._gptrigger_editing = True
        show_all = self._gt_filter_combo.currentData() == "all"
        entries = self._gptrigger_entries
        filtered = entries if show_all else [e for e in entries if e['safe_zone_type'] != 0]
        self._gt_table.setRowCount(len(filtered))
        for row, e in enumerate(filtered):
            item = QTableWidgetItem(str(e['key']))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setData(Qt.UserRole, e)
            self._gt_table.setItem(row, 0, item)
            name = e['name'].replace('GamePlayTrigger_SafeZone_', '').replace('GamePlayTrigger_', '')
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._gt_table.setItem(row, 1, item)
            item = QTableWidgetItem(f"[{e['flag1']},{e['flag2']},{e['flag3']}]")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._gt_table.setItem(row, 2, item)
            szt = e['safe_zone_type']
            item = QTableWidgetItem(str(szt))
            orig_szt = self._gptrigger_original[e['safe_zone_type_offset']]
            if szt != orig_szt:
                item.setBackground(QColor(80, 20, 60))
            if szt != 0:
                item.setForeground(QColor(255, 180, 80))
            self._gt_table.setItem(row, 3, item)
        self._gt_table.resizeColumnsToContents()
        self._gptrigger_editing = False

    def _regioninfo_populate(self):
        self._regioninfo_editing = True
        show_all = self._ri_filter_combo.currentData() == "all"
        entries = self._regioninfo_entries
        filtered = entries if show_all else [e for e in entries if e.get('_isTown', 0) or e.get('_limitVehicleRun', 0)]
        region_types = {3: 'World', 4: 'Continent', 5: 'Territory', 6: 'Area', 7: 'Node', 8: 'SubNode'}
        self._ri_table.setRowCount(len(filtered))
        for row, e in enumerate(filtered):
            item = QTableWidgetItem(str(e.get('_key', '?')))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setData(Qt.UserRole, e)
            self._ri_table.setItem(row, 0, item)
            name = e.get('_stringKey', f"Region_{e.get('_key', '?')}")
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._ri_table.setItem(row, 1, item)
            rt = e.get('_regionType', 0)
            item = QTableWidgetItem(f"{rt} ({region_types.get(rt, '?')})")
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._ri_table.setItem(row, 2, item)
            town = e.get('_isTown', 0)
            item = QTableWidgetItem(str(town))
            if town:
                item.setForeground(QColor(255, 180, 80))
            self._ri_table.setItem(row, 3, item)
            lvr = e.get('_limitVehicleRun', 0)
            item = QTableWidgetItem(str(lvr))
            if lvr:
                item.setForeground(QColor(255, 80, 80))
            self._ri_table.setItem(row, 4, item)
            item = QTableWidgetItem(str(e.get('_isWild', 0)))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._ri_table.setItem(row, 5, item)
            vmat = e.get('_vehicleMercenaryAllowType', 0)
            item = QTableWidgetItem(str(vmat))
            self._ri_table.setItem(row, 6, item)
        self._ri_table.resizeColumnsToContents()
        self._regioninfo_editing = False

    def _ri_cell_changed(self, row, col):
        if self._regioninfo_editing or not self._regioninfo_entries:
            return
        if col not in (3, 4, 6):
            return
        item0 = self._ri_table.item(row, 0)
        if not item0: return
        e = item0.data(Qt.UserRole)
        if not e: return
        cell = self._ri_table.item(row, col)
        if not cell: return
        try:
            new_val = int(cell.text())
        except ValueError:
            return
        new_val = max(0, min(new_val, 255))
        from regioninfo_parser import parse_pabgh_index as ri_idx_fn
        idx_ri = ri_idx_fn(self._regioninfo_schema)
        entry_offset = idx_ri.get(e['_key'])
        if entry_offset is None:
            return
        p = entry_offset
        p += 2
        slen = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + slen
        p += 1
        p += 1; p += 8
        dslen = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + dslen
        p += 4
        rk_count = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + rk_count * 8
        p += 2
        cr_count = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + cr_count * 2
        p += 1; p += 1; p += 4; p += 1; p += 4
        off_limitVehicleRun = p; p += 1
        off_isTown = p; p += 1
        p += 1; p += 1; p += 1; p += 1
        off_vehicleMercAllowType = p
        offsets = {3: off_isTown, 4: off_limitVehicleRun, 6: off_vehicleMercAllowType}
        field_map = {3: '_isTown', 4: '_limitVehicleRun', 6: '_vehicleMercenaryAllowType'}
        abs_off = offsets[col]
        self._regioninfo_data[abs_off] = new_val
        e[field_map[col]] = new_val
        self._field_edit_modified = True
        self._field_edit_status.setText(
            f"Modified region {e.get('_stringKey', '?')} {field_map[col]}={new_val}")

    def _mount_populate(self):
        self._charinfo_editing = True
        from characterinfo_full_parser import MOUNT_VEHICLE_TYPES
        entries = self._charinfo_mount_entries
        self._mount_table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            item = QTableWidgetItem(e.get('name', '?'))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setData(Qt.UserRole, e)
            self._mount_table.setItem(row, 0, item)
            vtype = e.get('_vehicleInfo', 0)
            vname = MOUNT_VEHICLE_TYPES.get(vtype, str(vtype))
            item = QTableWidgetItem(vname)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._mount_table.setItem(row, 1, item)
            dur = e.get('_callMercenarySpawnDuration', 0)
            item = QTableWidgetItem(str(dur))
            orig_dur = struct.unpack_from('<Q', self._charinfo_original, e['_callMercenarySpawnDuration_offset'])[0]
            if dur != orig_dur:
                item.setBackground(QColor(60, 40, 20))
            if dur > 0:
                item.setForeground(QColor(255, 180, 80))
            self._mount_table.setItem(row, 2, item)
            cool = e.get('_callMercenaryCoolTime', 0)
            item = QTableWidgetItem(str(cool))
            orig_cool = struct.unpack_from('<Q', self._charinfo_original, e['_callMercenaryCoolTime_offset'])[0]
            if cool != orig_cool:
                item.setBackground(QColor(60, 40, 20))
            if cool > 0:
                item.setForeground(QColor(255, 100, 100))
            self._mount_table.setItem(row, 3, item)
            item = QTableWidgetItem(str(e.get('_mercenaryCoolTimeType', 0)))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._mount_table.setItem(row, 4, item)
        self._mount_table.resizeColumnsToContents()
        self._charinfo_editing = False

    def _mount_cell_changed(self, row, col):
        if self._charinfo_editing or not self._charinfo_mount_entries:
            return
        if col not in (2, 3):
            return
        item0 = self._mount_table.item(row, 0)
        if not item0: return
        e = item0.data(Qt.UserRole)
        if not e: return
        cell = self._mount_table.item(row, col)
        if not cell: return
        try:
            new_val = int(cell.text())
        except ValueError:
            return
        new_val = max(0, new_val)
        if col == 2:
            off = e['_callMercenarySpawnDuration_offset']
            struct.pack_into('<Q', self._charinfo_data, off, new_val)
            e['_callMercenarySpawnDuration'] = new_val
            label = f"duration={new_val}s"
        else:
            off = e['_callMercenaryCoolTime_offset']
            struct.pack_into('<Q', self._charinfo_data, off, new_val)
            e['_callMercenaryCoolTime'] = new_val
            label = f"cooldown={new_val}s"
        self._field_edit_modified = True
        self._field_edit_status.setText(f"Modified {e.get('name', '?')} {label}")


    def _field_edit_make_killable(self):
        if not self._charinfo_data or not self._charinfo_mount_entries:
            QMessageBox.information(self, tr("Make Killable"), tr("Load game data first (click Load FieldInfo)."))
            return
        reply = QMessageBox.question(
            self, tr("Make All NPCs Killable"),
            "This will set _isAttackable=1 and _invincibility=0\n"
            "on all non-mount characters in characterinfo.pabgb.\n\n"
            "Mounts are excluded (use Invincible Mounts for those).\n"
            "Killing quest-essential NPCs may break story progression.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        from characterinfo_full_parser import parse_all_entries as ci_parse_all
        all_ci = ci_parse_all(bytes(self._charinfo_data), self._charinfo_schema)
        made_attackable = 0
        made_vincible = 0
        skipped_mounts = 0
        for e in all_ci:
            if e.get('_vehicleInfo', 0) != 0 or e.get('name', '').startswith('Riding_'):
                skipped_mounts += 1
                continue
            att_off = e.get('_isAttackable_offset', -1)
            inv_off = e.get('_invincibility_offset', -1)
            if att_off >= 0 and e.get('_isAttackable', 1) == 0:
                self._charinfo_data[att_off] = 1
                made_attackable += 1
            if inv_off >= 0 and e.get('_invincibility', 0) == 1:
                self._charinfo_data[inv_off] = 0
                made_vincible += 1
        if made_attackable == 0 and made_vincible == 0:
            QMessageBox.information(self, tr("Make Killable"), tr("All NPCs are already attackable."))
            return
        self._field_edit_modified = True
        self._charinfo_mount_entries = [e for e in ci_parse_all(bytes(self._charinfo_data), self._charinfo_schema)
                                        if e.get('_vehicleInfo', 0) != 0
                                        or e.get('name', '').startswith('Riding_')]
        self._mount_populate()
        log.info("make_killable: attackable+=%d vincible+=%d skipped_mounts=%d (total_entries=%d)",
                 made_attackable, made_vincible, skipped_mounts, len(all_ci))
        self._field_edit_status.setText(
            f"Made killable: {made_attackable} attackable + {made_vincible} vincible ({skipped_mounts} mounts skipped)")


    def _ally_ensure_loaded(self) -> bool:
        if (self._allygroup_data is not None and self._relationinfo_data is not None
                and self._factionrelgrp_data is not None):
            return True
        game_path = self._config.get("game_install_path", "")
        if not game_path or not os.path.isdir(game_path):
            QMessageBox.critical(self, tr("Game Path"),
                                 tr("Set the game install path first."))
            return False
        try:
            import crimson_rs
            dp = "gamedata/binary__/client/bin"
            if self._allygroup_data is None:
                ag_body = crimson_rs.extract_file(game_path, "0008", dp, "allygroupinfo.pabgb")
                ag_schema = crimson_rs.extract_file(game_path, "0008", dp, "allygroupinfo.pabgh")
                self._allygroup_data = bytearray(ag_body)
                self._allygroup_original = bytes(ag_body)
                self._allygroup_schema = bytes(ag_schema)
            if self._relationinfo_data is None:
                ri_body = crimson_rs.extract_file(game_path, "0008", dp, "relationinfo.pabgb")
                ri_schema = crimson_rs.extract_file(game_path, "0008", dp, "relationinfo.pabgh")
                self._relationinfo_data = bytearray(ri_body)
                self._relationinfo_original = bytes(ri_body)
                self._relationinfo_schema = bytes(ri_schema)
            if self._factionrelgrp_data is None:
                fg_body = crimson_rs.extract_file(game_path, "0008", dp, "factionrelationgroup.pabgb")
                fg_schema = crimson_rs.extract_file(game_path, "0008", dp, "factionrelationgroup.pabgh")
                self._factionrelgrp_data = bytearray(fg_body)
                self._factionrelgrp_original = bytes(fg_body)
                self._factionrelgrp_schema = bytes(fg_schema)
        except Exception as e:
            QMessageBox.critical(self, tr("Extract Failed"),
                                 f"Could not extract ally/relation/faction pabgbs:\n{e}")
            return False
        return True

    def _parse_ally_index(self) -> list[tuple[int, int]]:
        s = self._allygroup_schema
        count = struct.unpack_from("<H", s, 0)[0]
        kw = (len(s) - 2 - count * 4) // count
        out = []
        p = 2
        for _ in range(count):
            key = int.from_bytes(s[p:p + kw], "little")
            off = struct.unpack_from("<I", s, p + kw)[0]
            out.append((key, off))
            p += kw + 4
        return out

    def _parse_relation_index(self) -> list[tuple[int, int]]:
        s = self._relationinfo_schema
        count = struct.unpack_from("<H", s, 0)[0]
        kw = (len(s) - 2 - count * 4) // count
        out = []
        p = 2
        for _ in range(count):
            key = int.from_bytes(s[p:p + kw], "little")
            off = struct.unpack_from("<I", s, p + kw)[0]
            out.append((key, off))
            p += kw + 4
        return out

    def _field_edit_all_hostile(self):
        if not self._ally_ensure_loaded():
            return
        reply = QMessageBox.question(
            self, tr("Path A — All NPCs Hostile"),
            "Set RelationInfo._order = 99 on all 45 entries?\n\n"
            "Everyone becomes max-hostile to everyone. Your mounts will\n"
            "damage guards. Side effect: town NPCs may attack each other.\n\n"
            "Fully reversible via Restore. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        flipped = 0
        before: list[int] = []
        after: list[int] = []
        for _key, offset in self._parse_relation_index():
            p = offset + 1
            if p + 4 > len(self._relationinfo_data):
                continue
            slen = struct.unpack_from("<I", self._relationinfo_data, p)[0]
            if slen > 200:
                continue
            order_pos = offset + 1 + 4 + slen + 1 + 1
            if order_pos < len(self._relationinfo_data):
                before.append(self._relationinfo_data[order_pos])
                if self._relationinfo_data[order_pos] != 99:
                    self._relationinfo_data[order_pos] = 99
                    flipped += 1
                after.append(self._relationinfo_data[order_pos])
        log.info("path_a: flipped=%d/45, before=%s, after=%s",
                 flipped, sorted(set(before)), sorted(set(after)))
        self._field_edit_status.setText(
            tr(f"Path A: set _order=99 on {flipped} relation entries. Click Apply."))

    def _field_edit_wipe_ally_lists(self):
        if not self._ally_ensure_loaded():
            return
        reply = QMessageBox.question(
            self, tr("Path B — Wipe Ally Lists"),
            "Zero out _addOnAllyGroupList (list #0) hashes on all 50 groups?\n\n"
            "Effect: no faction is allied with any other. Guards still\n"
            "function (combat rules intact) but mutual defense is gone.\n\n"
            "Fully reversible via Restore. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        zeroed = 0
        for _key, offset in self._parse_ally_index():
            p = offset + 4
            slen = struct.unpack_from("<I", self._allygroup_data, p)[0]
            p += 4 + slen + 1
            if p + 4 > len(self._allygroup_data):
                continue
            cnt = struct.unpack_from("<I", self._allygroup_data, p)[0]
            if cnt > 200:
                continue
            hash_start = p + 4
            hash_end = hash_start + cnt * 4
            if hash_end <= len(self._allygroup_data):
                for i in range(hash_start, hash_end):
                    self._allygroup_data[i] = 0
                zeroed += cnt
        self._field_edit_status.setText(
            tr(f"Path B: zeroed {zeroed} ally-group hashes across 50 entries. Click Apply."))

    def _field_edit_set_ally_flag(self, slot: int):
        if slot < 0 or slot > 4:
            return
        if not self._ally_ensure_loaded():
            return
        reply = QMessageBox.question(
            self, tr(f"Path C — Flag Slot {slot}"),
            f"Set u8 flag #{slot} = 1 on all 50 AllyGroup entries?\n\n"
            f"Experiment: one of the 5 flag slots is _isIntruder. Try this\n"
            f"and tell me if NPCs attack each other.\n\n"
            f"Fully reversible via Restore. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        set_count = 0
        for _key, offset in self._parse_ally_index():
            p = offset + 4
            slen = struct.unpack_from("<I", self._allygroup_data, p)[0]
            p += 4 + slen + 1
            try:
                for _ in range(7):
                    cnt = struct.unpack_from("<I", self._allygroup_data, p)[0]
                    if cnt > 200:
                        raise ValueError("bad list count")
                    p += 4 + cnt * 4
                target = p + slot
                if target < len(self._allygroup_data):
                    self._allygroup_data[target] = 1
                    set_count += 1
            except Exception:
                continue
        self._field_edit_status.setText(
            tr(f"Path C: set flag #{slot}=1 on {set_count} ally groups. Click Apply."))

    def _parse_factionrelgrp_index(self) -> list[tuple[int, int]]:
        s = self._factionrelgrp_schema
        count = struct.unpack_from("<H", s, 0)[0]
        kw = (len(s) - 2 - count * 4) // count
        out = []
        p = 2
        for _ in range(count):
            key = int.from_bytes(s[p:p + kw], "little")
            off = struct.unpack_from("<I", s, p + kw)[0]
            out.append((key, off))
            p += kw + 4
        return out

    def _field_edit_wipe_faction_relation_allies(self, all_lists: bool = False):
        if not self._ally_ensure_loaded():
            return
        label = "ALL 4 LISTS" if all_lists else "list #0 (allied)"
        reply = QMessageBox.question(
            self, tr(f"Path D — Wipe FactionRelationGroup {label}"),
            f"Zero the hash bytes in {label} across all 5 FactionRelationGroup entries?\n\n"
            f"Target: the 5 top-level groups (Civilian/Guard/Bandit/Player/...).\n"
            f"This is the LEVEL ABOVE allygroupinfo — where guard immunity lives.\n\n"
            f"Fully reversible via Restore. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        buf = self._factionrelgrp_data
        zeroed_entries = 0
        zeroed_hashes = 0
        list_counts_seen: list[tuple[int, int, int, int]] = []
        for key, offset in self._parse_factionrelgrp_index():
            try:
                p = offset + 2
                slen = struct.unpack_from("<I", buf, p)[0]
                p += 4 + slen
                if slen > 200:
                    log.warning("path_d: entry %d has bad slen=%d, skipping", key, slen)
                    continue
                p += 1
                counts = []
                for list_idx in range(4):
                    if p + 4 > len(buf):
                        raise ValueError(f"truncated at list {list_idx}")
                    cnt = struct.unpack_from("<I", buf, p)[0]
                    if cnt > 500:
                        raise ValueError(f"bad cnt={cnt} at list {list_idx}")
                    counts.append(cnt)
                    hash_start = p + 4
                    hash_end = hash_start + cnt * 2
                    if hash_end > len(buf):
                        raise ValueError(f"truncated hashes at list {list_idx}")
                    if (all_lists or list_idx == 0) and cnt > 0:
                        for i in range(hash_start, hash_end):
                            buf[i] = 0
                        zeroed_hashes += cnt
                    p = hash_end
                list_counts_seen.append(tuple(counts))
                zeroed_entries += 1
            except Exception as e:
                log.exception("path_d: failed on entry key=%d offset=%d: %s",
                              key, offset, e)
        log.info("path_d: wiped %s on %d/5 entries; %d u16 hashes zeroed; per-entry counts=%s",
                 label, zeroed_entries, zeroed_hashes, list_counts_seen)
        self._field_edit_status.setText(
            tr(f"Path D: wiped {label} on {zeroed_entries} entries "
               f"({zeroed_hashes} hashes zeroed). Click Apply."))

    def _field_edit_invincible_mounts(self):
        if not self._charinfo_data or not self._charinfo_schema:
            QMessageBox.information(self, tr("Invincible Mounts"), tr("Load game data first (click Load FieldInfo)."))
            return
        from characterinfo_full_parser import parse_all_entries as ci_parse_all
        all_ci = ci_parse_all(bytes(self._charinfo_data), self._charinfo_schema)
        count = 0
        for e in all_ci:
            if e.get('_vehicleInfo', 0) == 0 and not e.get('name', '').startswith('Riding_'):
                continue
            inv_off = e.get('_invincibility_offset', -1)
            if inv_off >= 0 and e.get('_invincibility', 0) == 0:
                self._charinfo_data[inv_off] = 1
                count += 1
        if count == 0:
            QMessageBox.information(self, tr("Invincible Mounts"), tr("All mounts are already invincible."))
            return
        self._field_edit_modified = True
        self._charinfo_mount_entries = [e for e in ci_parse_all(bytes(self._charinfo_data), self._charinfo_schema)
                                        if e.get('_vehicleInfo', 0) != 0
                                        or e.get('name', '').startswith('Riding_')]
        self._mount_populate()
        log.info("invincible_mounts: patched=%d", count)
        self._field_edit_status.setText(f"Made {count} mounts invincible")

    def _field_edit_enable_mounts(self):
        log.info("=== enable_mounts: starting ===")
        if not self._vehicle_data:
            log.warning("enable_mounts: _vehicle_data is None — can't patch vehicleinfo")
        if not self._regioninfo_data:
            log.warning("enable_mounts: _regioninfo_data is None — can't patch regioninfo "
                        "(regioninfo_parser import probably failed)")
        if not self._regioninfo_entries:
            log.warning("enable_mounts: _regioninfo_entries is EMPTY (parser failed or file missing)")
        if not self._charinfo_data:
            log.warning("enable_mounts: _charinfo_data is None — can't patch characterinfo")
        if not self._vehicle_data and not self._regioninfo_data and not self._charinfo_data:
            QMessageBox.information(self, tr("FieldEdit"), tr("Load FieldInfo first."))
            return
        v_count = 0
        if self._vehicle_data and self._vehicle_entries:
            for e in self._vehicle_entries:
                off = e.get('can_call_safe_zone_offset', -1)
                if off >= 0 and e.get('can_call_safe_zone', 0) == 0:
                    self._vehicle_data[off] = 1
                    e['can_call_safe_zone'] = 1
                    v_count += 1
            self._vehicle_populate()
        ri_count = 0
        if self._regioninfo_data and self._regioninfo_entries:
            from regioninfo_parser import parse_pabgh_index as ri_idx_fn
            ri_idx = ri_idx_fn(self._regioninfo_schema)
            for e in self._regioninfo_entries:
                if e.get('_limitVehicleRun', 0) or e.get('_isTown', 0):
                    entry_off = ri_idx.get(e['_key'])
                    if entry_off is not None:
                        p = entry_off
                        p += 2
                        slen = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + slen
                        p += 1
                        p += 1; p += 8
                        dslen = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + dslen
                        p += 4
                        rk_c = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + rk_c * 8
                        p += 2
                        cr_c = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + cr_c * 2
                        p += 2; p += 4; p += 1; p += 4
                        if e.get('_limitVehicleRun', 0):
                            self._regioninfo_data[p] = 0
                            e['_limitVehicleRun'] = 0
                            ri_count += 1
                        if e.get('_isTown', 0):
                            self._regioninfo_data[p + 1] = 0
                            e['_isTown'] = 0
                            ri_count += 1
            self._regioninfo_populate()
        mount_count = 0
        patched_names: list[str] = []
        candidates = 0
        if self._charinfo_data and self._charinfo_mount_entries:
            for e in self._charinfo_mount_entries:
                dur = e.get('_callMercenarySpawnDuration', 0)
                cool = e.get('_callMercenaryCoolTime', 0)
                if dur > 0 or cool > 0:
                    candidates += 1
                hit = False
                if dur > 0:
                    off = e['_callMercenarySpawnDuration_offset']
                    struct.pack_into('<Q', self._charinfo_data, off, 0x7FFFFFFF)
                    e['_callMercenarySpawnDuration'] = 0x7FFFFFFF
                    mount_count += 1; hit = True
                if cool > 0:
                    off = e['_callMercenaryCoolTime_offset']
                    struct.pack_into('<Q', self._charinfo_data, off, 0)
                    e['_callMercenaryCoolTime'] = 0
                    mount_count += 1; hit = True
                if hit:
                    patched_names.append(e.get('name', '?'))
            self._mount_populate()
        self._field_edit_modified = True
        log.info("enable_mounts: vehicle=%d region=%d mount=%d edits applied "
                 "(%d/%d mount-entries had dur>0 or cool>0; patched: %s)",
                 v_count, ri_count, mount_count,
                 candidates, len(self._charinfo_mount_entries or []),
                 ", ".join(patched_names) if patched_names else "(none)")
        self._field_edit_status.setText(
            f"Enabled: {v_count} vehicle flags + {ri_count} region dismounts + {mount_count} mount limits")


    def _field_edit_open_mesh_swap(self) -> None:
        if not self._charinfo_data:
            QMessageBox.information(
                self, tr("Mesh Swap"),
                "Click 'Load FieldInfo' first — we need characterinfo.pabgb "
                "loaded into memory before we can queue mesh swaps.")
            return

        catalog: list = []
        for base in [os.path.dirname(os.path.abspath(__file__)),
                     getattr(sys, '_MEIPASS', '') or '',
                     os.getcwd()]:
            path = os.path.join(base, 'character_catalog.json')
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        catalog = json.load(f).get('characters', []) or []
                    break
                except Exception:
                    continue
        if not catalog:
            try:
                from data_db import get_connection
                conn = get_connection()
                rows = conn.execute(
                    "SELECT character_key, internal_name, display_name, category "
                    "FROM characters"
                ).fetchall()
                for r in rows:
                    catalog.append({
                        'character_key': int(r['character_key']),
                        'internal_name': r['internal_name'] or '',
                        'display_name': r['display_name'] or '',
                        'category': r['category'] or 'Other',
                    })
            except Exception as e:
                log.debug("characters SQLite fallback failed: %s", e)

        if not catalog:
            QMessageBox.warning(
                self, tr("Mesh Swap"),
                "character_catalog.json / characters DB not found. Run _dump_characters.py to "
                "generate it (6872 characters expected).")
            return

        # Load the authoritative pet catalog (derived from Ghost Hunter's
        # PetRenamerMod v1.2.0 — 40 named pets/mounts × 216 char_keys).
        pet_keys: dict[int, str] = {}  # char_key -> group ('Horse'/'Dog'/'Cat'/'Special Mount')
        pet_display: dict[int, str] = {}  # char_key -> pretty name
        for base in [os.path.dirname(os.path.abspath(__file__)),
                     os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'),
                     os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                     os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data'),
                     os.path.join(getattr(sys, '_MEIPASS', ''), 'data'),
                     getattr(sys, '_MEIPASS', '') or '',
                     os.path.join(os.getcwd(), 'data'),
                     os.getcwd()]:
            ppath = os.path.join(base, 'pet_catalog.json')
            if os.path.isfile(ppath):
                try:
                    with open(ppath, 'r', encoding='utf-8') as pf:
                        pdata = json.load(pf)
                    for group, names in (pdata.get('groups') or {}).items():
                        for pname, keys in names.items():
                            for k in (keys or []):
                                pet_keys[int(k)] = group
                                pet_display[int(k)] = pname
                    log.info("Loaded pet_catalog: %d pet keys across %d groups",
                             len(pet_keys), len(pdata.get('groups') or {}))
                    break
                except Exception as e:
                    log.warning("pet_catalog load failed: %s", e)

        # Re-categorize with friendlier buckets so Pet / Mount / Animal stand
        # out from the 6000+ generic "Other" characters. Authoritative pet
        # catalog takes priority; name-heuristic is fallback for unlisted.
        PET_WORDS = ('_cat', 'cat_', 'dog', 'puppy', 'kitten', 'chicken',
                     'rabbit', 'bunny', 'fox', 'squirrel', 'mouse_',
                     'lizard', 'turtle', 'parrot', 'frog', 'sheep_lamb',
                     'ducks', 'duckling', 'hamster')
        ANIMAL_WORDS = ('wolf', 'bear', 'tiger', 'lion', 'boar', 'deer',
                        'moose', 'elk', 'goat', 'sheep', 'cow', 'donkey',
                        'camel', 'snake', 'eagle', 'hawk', 'owl', 'wyvern',
                        'horse', 'hound', 'serpent', 'spider', 'scorpion',
                        'crow_', 'raven')
        HERO_NAMES = ('kliff', 'damian', 'oongka', 'macduff', 'ziane',
                      'macgyver')
        for c in catalog:
            existing = c.get('category', 'Other')
            ck = int(c.get('character_key', 0))
            internal = (c.get('internal_name') or '').lower()
            display = (c.get('display_name') or '').lower()
            name_mash = internal + ' ' + display

            # Authoritative pet catalog takes priority
            if ck in pet_keys:
                group = pet_keys[ck]
                if group == 'Horse':
                    c['category'] = 'Mount (Horse)'
                elif group == 'Dog':
                    c['category'] = 'Pet (Dog)'
                elif group == 'Cat':
                    c['category'] = 'Pet (Cat)'
                elif group == 'Special Mount':
                    c['category'] = 'Mount (Special)'
                else:
                    c['category'] = 'Pet'
                # Override display to the friendly pet name for clarity
                pretty = pet_display.get(ck)
                if pretty and not (c.get('display_name') or '').strip():
                    c['display_name'] = pretty
                continue

            if existing in ('Boss', 'MiddleBoss'):
                c['category'] = 'Boss'
            elif existing == 'Dragon':
                c['category'] = 'Dragon'
            elif existing == 'Mount' or internal.startswith('riding_'):
                c['category'] = 'Mount'
            elif any(h == display or h == internal for h in HERO_NAMES):
                c['category'] = 'Hero'
            elif any(w in name_mash for w in PET_WORDS):
                c['category'] = 'Pet'
            elif any(w in name_mash for w in ANIMAL_WORDS):
                c['category'] = 'Animal'
            elif existing == 'NPC':
                c['category'] = 'NPC'
            # else keep 'Other'

        saved = self._config.get('mesh_swap_queue') or []
        if not self._mesh_swap_queue and saved:
            self._mesh_swap_queue = [
                {'src': int(s['src']), 'tgt': int(s['tgt']),
                 'scale': float(s.get('scale', 0) or 0),
                 'rideable': bool(s.get('rideable', False)),
                 'rider_y': float(s.get('rider_y', 8.0) or 8.0)}
                for s in saved if isinstance(s, dict) and 'src' in s and 'tgt' in s
            ]

        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Mesh Swap — Character Visual Transmog"))
        dlg.resize(1000, 800)
        dlg.setSizeGripEnabled(True)
        dlg_layout = QVBoxLayout(dlg)

        tutorial = QLabel(
            "<b>How to use Mesh Swap (Visual Transmog)</b><br>"
            "<b>1.</b> Pick what you want to change on the <b>LEFT</b> (Target) "
            "— e.g. your horse, your pet cat, an NPC.<br>"
            "<b>2.</b> Pick what you want it to <b>look like</b> on the <b>RIGHT</b> "
            "(Source) — e.g. a wolf, a dragon, another NPC.<br>"
            "<b>3.</b> Click <b>Add Swap</b>. Repeat for as many swaps as you want.<br>"
            "<b>4.</b> Close this dialog and click <b>Apply to Game</b> in the Field Edit tab "
            "(or <b>Export as Mod</b>).<br>"
            "<b>Tip:</b> Use the <b>Category</b> dropdown to narrow down to Pets, Mounts, "
            "Animals, Heroes, or Bosses. Use the search boxes to find a specific character by name.")
        tutorial.setWordWrap(True)
        tutorial.setTextFormat(Qt.RichText)
        tutorial.setStyleSheet(
            f"color: {COLORS['text']}; background-color: {COLORS['panel']}; "
            f"padding: 10px; border: 2px solid {COLORS['accent']}; "
            f"border-radius: 6px; font-size: 12px;")
        dlg_layout.addWidget(tutorial)

        info = QLabel(
            "Swap one character's visual mesh with another's. Same 0039/ overlay "
            "as Field Edit — applied on Apply-to-Game / Export.")
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color: {COLORS['text_dim']}; padding: 4px; font-size: 10px;")
        dlg_layout.addWidget(info)

        CATEGORY_ORDER = ['All',
                          'Pet (Cat)', 'Pet (Dog)', 'Pet',
                          'Animal',
                          'Mount (Horse)', 'Mount (Special)', 'Mount',
                          'Hero', 'Dragon', 'Boss', 'NPC', 'Other']
        cats_present = {c.get('category', 'Other') for c in catalog}
        def _make_cat_combo():
            cb = QComboBox()
            for key in CATEGORY_ORDER:
                if key == 'All' or key in cats_present:
                    cb.addItem(key)
            cb.setToolTip(
                "Pet = cats, dogs, bunnies, chickens (small companions).\n"
                "Animal = wolves, bears, tigers (wild / swap sources).\n"
                "Mount = rideable (horses, camels, wolves, wagons).\n"
                "Hero = playable main characters.\n"
                "Dragon / Boss / NPC / Other — self-explanatory.\n\n"
                "Target and Source filters are INDEPENDENT — filter the left\n"
                "to Pet (Cat) and the right to Dragon to make cats look like "
                "wyverns.")
            return cb
        tgt_cat_combo = _make_cat_combo()
        src_cat_combo = _make_cat_combo()

        # ─── Quick-Swap Presets row ────────────────────────────────────────
        presets_row = QHBoxLayout()
        presets_lbl = QLabel("Quick Swap Helpers:")
        presets_lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        presets_row.addWidget(presets_lbl)

        def _show_filter(target_cat: str, source_cat: str):
            # Set Target panel to the narrow filter, Source panel to the wider
            # suggestion (user can pick any source they want).
            ti = tgt_cat_combo.findText(target_cat)
            if ti >= 0:
                tgt_cat_combo.setCurrentIndex(ti)
            si = src_cat_combo.findText(source_cat)
            if si >= 0:
                src_cat_combo.setCurrentIndex(si)
            QMessageBox.information(
                dlg, "Quick Swap Tip",
                f"Target filter set to <b>{target_cat}</b> (left panel).<br>"
                f"Source filter set to <b>{source_cat}</b> (right panel) — "
                f"change it on the right if you want something different.<br><br>"
                f"1. Pick the specific character on the LEFT.<br>"
                f"2. Pick what to look like on the RIGHT "
                f"(use the source search or change its category to 'All' for full freedom).<br>"
                f"3. Click <b>Add Swap</b>.")

        cat_btn = QPushButton("🐱 Cats →")
        cat_btn.setToolTip(
            "Guided helper: filter Target to Pet (Cat) — 11 named cat breeds "
            "from Ghost Hunter's catalog. Then pick what you want them to look "
            "like (wolves, bears, dragons...) on the RIGHT.")
        cat_btn.clicked.connect(lambda: _show_filter('Pet (Cat)', 'Animal'))
        presets_row.addWidget(cat_btn)

        dog_btn = QPushButton("🐶 Dogs →")
        dog_btn.setToolTip(
            "Guided helper: filter Target to Pet (Dog) — 12 named dog breeds "
            "(Beagle, Boarhound, Husky, etc). Then pick what you want them "
            "to look like on the RIGHT.")
        dog_btn.clicked.connect(lambda: _show_filter('Pet (Dog)', 'Animal'))
        presets_row.addWidget(dog_btn)

        horse_btn = QPushButton("🐴 Horses →")
        horse_btn.setToolTip(
            "Guided helper: filter Target to Mount (Horse) — named horse "
            "breeds (Priden, Royler, Camora, etc). Then pick what you want "
            "them to look like — Dragon / Special Mount / anything.")
        horse_btn.clicked.connect(lambda: _show_filter('Mount (Horse)', 'Dragon'))
        presets_row.addWidget(horse_btn)

        special_btn = QPushButton("✨ Special Mounts →")
        special_btn.setToolTip(
            "Guided helper: filter Target to Mount (Special) — the 5 unique "
            "mounts (Silver Fang, White Bear, Snowwhite Deer, Clawed Bear, "
            "Kuku Bird Hatchling). Swap their appearance to anything else.")
        special_btn.clicked.connect(lambda: _show_filter('Mount (Special)', 'Animal'))
        presets_row.addWidget(special_btn)

        presets_row.addStretch()
        dlg_layout.addLayout(presets_row)

        vsplitter = QSplitter(Qt.Vertical)
        dlg_layout.addWidget(vsplitter, 1)

        picker_wrap = QWidget()
        picker_wrap_layout = QVBoxLayout(picker_wrap)
        picker_wrap_layout.setContentsMargins(0, 0, 0, 0)
        cols = QHBoxLayout()

        def make_column(title: str, placeholder: str, cat_combo):
            box = QVBoxLayout()
            lbl = QLabel(title)
            lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
            box.addWidget(lbl)
            cat_row = QHBoxLayout()
            cat_row.addWidget(QLabel(tr("Filter:")))
            cat_row.addWidget(cat_combo, 1)
            box.addLayout(cat_row)
            search = QLineEdit()
            search.setPlaceholderText(placeholder)
            box.addWidget(search)
            lst = QListWidget()
            lst.setMinimumHeight(350)
            box.addWidget(lst, 1)
            return box, search, lst

        tgt_col, tgt_search, tgt_list = make_column(
            tr("Target (the character to re-skin)"),
            tr("Search name, internal name, or character key (e.g. 1000318)"),
            tgt_cat_combo)
        src_col, src_search, src_list = make_column(
            tr("Source (visual to copy — pick ANY character, any category)"),
            tr("Search name, internal name, or character key (e.g. 30507). Set filter to 'All' to browse everything."),
            src_cat_combo)
        cols.addLayout(tgt_col, 1)
        cols.addLayout(src_col, 1)
        picker_wrap_layout.addLayout(cols, 1)
        vsplitter.addWidget(picker_wrap)

        persistent_sel_style = (
            "QListWidget::item:selected:!active { "
            f"background-color: {COLORS['accent']}; color: black; }} "
            "QListWidget::item:selected:active { "
            f"background-color: {COLORS['accent']}; color: black; }}"
        )
        tgt_list.setStyleSheet(persistent_sel_style)
        src_list.setStyleSheet(persistent_sel_style)

        def populate(lst: QListWidget, search_text: str, cat_filter: str):
            prev_key = None
            cur = lst.currentItem()
            if cur is not None:
                prev_key = cur.data(Qt.UserRole)
            lst.clear()
            restore_row = -1
            q = (search_text or '').strip().lower()
            q_as_int = None
            if q.isdigit():
                try:
                    q_as_int = int(q)
                except ValueError:
                    pass
            for c in catalog:
                if cat_filter != 'All' and c.get('category') != cat_filter:
                    continue
                ck = int(c.get('character_key', 0))
                internal = (c.get('internal_name') or '').lower()
                display = (c.get('display_name') or '').lower()
                if q:
                    hit = False
                    if q_as_int is not None and q in str(ck):
                        hit = True
                    if not hit and (q in internal or q in display):
                        hit = True
                    if not hit:
                        continue
                disp = c.get('display_name') or ''
                internal_show = c.get('internal_name') or ''
                if disp and disp != internal_show:
                    label = (f"[{c.get('category', 'Other')}] {disp}"
                             f"  — {internal_show}  ({ck})")
                else:
                    label = f"[{c.get('category', 'Other')}] {internal_show}  ({ck})"
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, ck)
                lst.addItem(it)
                if prev_key is not None and ck == prev_key:
                    restore_row = lst.count() - 1
            if restore_row >= 0:
                lst.setCurrentRow(restore_row)

        tgt_search.textChanged.connect(
            lambda _: populate(tgt_list, tgt_search.text(), tgt_cat_combo.currentText()))
        src_search.textChanged.connect(
            lambda _: populate(src_list, src_search.text(), src_cat_combo.currentText()))
        tgt_cat_combo.currentTextChanged.connect(
            lambda _: populate(tgt_list, tgt_search.text(), tgt_cat_combo.currentText()))
        src_cat_combo.currentTextChanged.connect(
            lambda _: populate(src_list, src_search.text(), src_cat_combo.currentText()))
        populate(tgt_list, '', 'All')
        populate(src_list, '', 'All')

        queue_wrap = QWidget()
        queue_wrap_layout = QVBoxLayout(queue_wrap)
        queue_wrap_layout.setContentsMargins(0, 0, 0, 0)
        queue_lbl = QLabel(tr("Queued swaps (applied at Export/Apply time — drag divider above to resize):"))
        queue_lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        queue_wrap_layout.addWidget(queue_lbl)
        queue_list = QListWidget()
        queue_list.setMinimumHeight(80)
        queue_wrap_layout.addWidget(queue_list, 1)
        vsplitter.addWidget(queue_wrap)
        vsplitter.setSizes([520, 200])
        vsplitter.setStretchFactor(0, 3)
        vsplitter.setStretchFactor(1, 2)

        def _name_for(ck: int) -> str:
            for c in catalog:
                if c.get('character_key') == ck:
                    disp = c.get('display_name') or ''
                    internal = c.get('internal_name') or ''
                    if disp and disp != internal:
                        return f"{disp} ({internal})"
                    return internal or f"char {ck}"
            return f"char {ck}"

        def refresh_queue():
            queue_list.clear()
            for sw in self._mesh_swap_queue:
                sc = float(sw.get('scale') or 0)
                tk, sk = sw['tgt'], sw['src']
                tags = []
                if sc > 0:
                    tags.append(f"scale {sc:g}")
                if sw.get('rideable'):
                    tags.append(f"rideable Y={float(sw.get('rider_y', 8.0)):g}")
                tag_str = "   [" + ", ".join(tags) + "]" if tags else ""
                if tk == sk and sc > 0 and not sw.get('rideable'):
                    line = f"{_name_for(tk)}   ->   [SCALE ONLY, scale {sc:g}]"
                else:
                    line = (f"{_name_for(tk)}   ->   now looks like   ->   "
                            f"{_name_for(sk)}{tag_str}")
                queue_list.addItem(line)
            try:
                self._config['mesh_swap_queue'] = list(self._mesh_swap_queue)
                self.config_save_requested.emit()
            except Exception:
                pass

        refresh_queue()

        btn_row = QHBoxLayout()
        add_btn = QPushButton(tr("Add Swap"))
        add_btn.setObjectName("accentBtn")
        remove_btn = QPushButton(tr("Remove Selected"))
        clear_btn = QPushButton(tr("Clear All"))
        change_all_btn = QPushButton(tr("Change All (visible targets)"))
        change_all_btn.setToolTip(
            "Replace the appearance of EVERY character currently visible in "
            "the Target panel with the selected Source's appearance. "
            "Respects current category/search filter.")
        scale_spin = QDoubleSpinBox()
        scale_spin.setRange(0.0, 10.0)
        scale_spin.setSingleStep(0.1)
        scale_spin.setDecimals(1)
        scale_spin.setValue(0.0)
        scale_spin.setToolTip("Character scale override (0 = keep default)")
        scale_only_btn = QPushButton(tr("Scale Only"))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(change_all_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        dlg_layout.addLayout(btn_row)

        # ── Mount options row: scale + rideable ──
        mount_row = QHBoxLayout()
        mount_row.addWidget(QLabel("Scale (0=default):"))
        mount_row.addWidget(scale_spin)
        mount_row.addWidget(scale_only_btn)
        mount_row.addSpacing(20)
        rideable_cb = QCheckBox("Make Rideable")
        rideable_cb.setToolTip(
            "Inject B_Rider_01 bone into the source skeleton so you can ride it.\n"
            "Only confirmed working for flying dragon-type mounts\n"
            "(e.g. Golden Star swapped onto Blackstar).")
        def _on_rideable_toggled(checked):
            rider_y_spin.setEnabled(checked)
            if checked:
                QMessageBox.information(
                    dlg, "Make Rideable — Limitation",
                    "This feature currently only works reliably for "
                    "flying dragon-type mounts.\n\n"
                    "Example: swap Golden Star onto Blackstar (both dragons, "
                    "same mount controller).\n\n"
                    "Cross-family swaps (spider on horse, human on mount, etc.) "
                    "will NOT produce rideable results — the mount movement "
                    "controller is tied to the target character type, not the "
                    "visual mesh.\n\n"
                    "Scale overrides work for any mount regardless.")
        rideable_cb.toggled.connect(_on_rideable_toggled)
        mount_row.addWidget(rideable_cb)
        mount_row.addWidget(QLabel("Rider Height:"))
        rider_y_spin = QDoubleSpinBox()
        rider_y_spin.setRange(0.0, 20.0)
        rider_y_spin.setSingleStep(0.5)
        rider_y_spin.setDecimals(1)
        rider_y_spin.setValue(8.0)
        rider_y_spin.setEnabled(False)
        rider_y_spin.setToolTip(
            "Y offset for rider seat position (relative to Spine1 bone).\n"
            "Higher = rider sits higher above the mount's body.\n"
            "Golden Star: ~8.0, Wyvern: ~4.0, Wolf: ~2.0")
        mount_row.addWidget(rider_y_spin)
        mount_row.addStretch()
        dlg_layout.addLayout(mount_row)

        cfg_row = QHBoxLayout()
        export_btn = QPushButton(tr("Export Config"))
        import_btn = QPushButton(tr("Import Config"))
        close_btn = QPushButton(tr("Close"))
        cfg_row.addWidget(export_btn)
        cfg_row.addWidget(import_btn)
        cfg_row.addStretch()
        cfg_row.addWidget(close_btn)
        dlg_layout.addLayout(cfg_row)

        def on_add():
            ti = tgt_list.currentItem()
            si = src_list.currentItem()
            if not ti or not si:
                QMessageBox.information(dlg, tr("Mesh Swap"),
                    "Pick one TARGET (left panel) and one SOURCE (right panel) before adding.")
                return
            tk = ti.data(Qt.UserRole)
            sk = si.data(Qt.UserRole)
            scale = float(scale_spin.value())
            rideable = rideable_cb.isChecked()
            rider_y = float(rider_y_spin.value())
            if tk == sk and scale <= 0 and not rideable:
                QMessageBox.information(dlg, tr("Mesh Swap"),
                    "Target and source are the same and no scale override or rideable is set. "
                    "Either pick a different source, or raise the Scale value above 0 "
                    "to queue a scale-only entry (alternatively use 'Scale Only' button).")
                return
            self._mesh_swap_queue[:] = [s for s in self._mesh_swap_queue if s['tgt'] != tk]
            self._mesh_swap_queue.append(
                {'src': int(sk), 'tgt': int(tk), 'scale': scale,
                 'rideable': rideable, 'rider_y': rider_y})
            refresh_queue()

        def on_scale_only():
            scale = float(scale_spin.value())
            if scale <= 0:
                QMessageBox.information(dlg, tr("Scale Only"),
                    "Raise the Scale value above 0 first. Scale Only needs a "
                    "positive override value — it does nothing with scale=0.")
                return
            ti = tgt_list.currentItem()
            si = src_list.currentItem()
            pick = ti or si
            if not pick:
                QMessageBox.information(dlg, tr("Scale Only"),
                    "Select a character in either the TARGET or SOURCE panel first.")
                return
            ck = int(pick.data(Qt.UserRole))
            self._mesh_swap_queue[:] = [s for s in self._mesh_swap_queue if s['tgt'] != ck]
            self._mesh_swap_queue.append(
                {'src': ck, 'tgt': ck, 'scale': scale,
                 'rideable': False, 'rider_y': 8.0})
            refresh_queue()

        def on_change_all():
            si = src_list.currentItem()
            if not si:
                QMessageBox.information(dlg, tr("Mesh Swap"),
                    "Pick one SOURCE (right panel) first. Change All will add "
                    "a swap entry for every character currently visible in the "
                    "Target panel (respects your category/search filter).")
                return
            sk = int(si.data(Qt.UserRole))
            target_keys = []
            for i in range(tgt_list.count()):
                it = tgt_list.item(i)
                if it is None:
                    continue
                tk = int(it.data(Qt.UserRole))
                if tk == sk:
                    continue
                target_keys.append(tk)
            if not target_keys:
                QMessageBox.information(dlg, tr("Mesh Swap"),
                    "No targets currently visible. Clear or adjust filters first.")
                return
            scale = float(scale_spin.value())
            scale_note = f" (scale {scale:g})" if scale > 0 else ""
            ans = QMessageBox.question(
                dlg, tr("Change All"),
                f"Replace the appearance of ALL {len(target_keys)} visible target(s) "
                f"with '{_name_for(sk)}'{scale_note}?\n\n"
                "This overwrites existing queue entries for those targets.",
                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if ans != QMessageBox.Yes:
                return
            existing_by_tgt = {s['tgt']: s for s in self._mesh_swap_queue}
            added = updated = 0
            for tk in target_keys:
                if tk in existing_by_tgt:
                    updated += 1
                else:
                    added += 1
                existing_by_tgt[tk] = {'src': sk, 'tgt': tk, 'scale': scale,
                                       'rideable': rideable_cb.isChecked(),
                                       'rider_y': float(rider_y_spin.value())}
            self._mesh_swap_queue[:] = list(existing_by_tgt.values())
            refresh_queue()
            QMessageBox.information(dlg, tr("Change All"),
                f"Added {added}, updated {updated}. Queue now has "
                f"{len(self._mesh_swap_queue)} swap(s).")

        def on_remove():
            row = queue_list.currentRow()
            if 0 <= row < len(self._mesh_swap_queue):
                del self._mesh_swap_queue[row]
                refresh_queue()

        def on_clear():
            self._mesh_swap_queue.clear()
            refresh_queue()

        def on_export():
            path, _ = QFileDialog.getSaveFileName(
                dlg, tr("Export Mesh Swap Config"), "mesh_swap_config.json", "JSON (*.json)")
            if not path:
                return
            out = {
                'version': 2,
                'kind': 'character_mesh_swap',
                'swaps': [
                    {
                        'target_key': s['tgt'],
                        'target_name': _name_for(s['tgt']),
                        'source_key': s['src'],
                        'source_name': _name_for(s['src']),
                        'scale': float(s.get('scale') or 0),
                        'rideable': bool(s.get('rideable', False)),
                        'rider_y': float(s.get('rider_y', 8.0) or 8.0),
                    }
                    for s in self._mesh_swap_queue
                ],
            }
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(out, f, indent=2, ensure_ascii=False)
                QMessageBox.information(dlg, tr("Export"),
                    f"Wrote {len(self._mesh_swap_queue)} swap(s) to:\n{path}")
            except Exception as e:
                QMessageBox.critical(dlg, tr("Export Failed"), str(e))

        def on_import():
            path, _ = QFileDialog.getOpenFileName(
                dlg, tr("Import Mesh Swap Config"), "", "JSON (*.json)")
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception as e:
                QMessageBox.critical(dlg, tr("Import Failed"), str(e))
                return
            if cfg.get('kind') not in ('character_mesh_swap', None):
                QMessageBox.warning(dlg, tr("Import"),
                    f"File is '{cfg.get('kind', 'unknown')}' — expected 'character_mesh_swap'.")
                return
            entries = cfg.get('swaps') or []
            if not entries:
                QMessageBox.information(dlg, tr("Import"), tr("Config contains no swaps."))
                return
            if self._mesh_swap_queue:
                btn = QMessageBox.question(
                    dlg, tr("Import"),
                    f"Current queue has {len(self._mesh_swap_queue)} swap(s).\n\n"
                    f"Yes = Replace queue with {len(entries)} imported\n"
                    f"No  = Append imported to current queue\n"
                    f"Cancel",
                    QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                    QMessageBox.No)
                if btn == QMessageBox.Cancel:
                    return
                if btn == QMessageBox.Yes:
                    self._mesh_swap_queue.clear()
            by_key = {c['character_key']: c for c in catalog}
            added = missed = 0
            for s in entries:
                tk = s.get('target_key')
                sk = s.get('source_key')
                sc = float(s.get('scale') or 0)
                if tk is None or sk is None or tk not in by_key or sk not in by_key:
                    missed += 1
                    continue
                self._mesh_swap_queue[:] = [x for x in self._mesh_swap_queue if x['tgt'] != tk]
                self._mesh_swap_queue.append(
                    {'src': int(sk), 'tgt': int(tk), 'scale': sc,
                     'rideable': bool(s.get('rideable', False)),
                     'rider_y': float(s.get('rider_y', 8.0) or 8.0)})
                added += 1
            refresh_queue()
            QMessageBox.information(dlg, tr("Import"),
                f"Imported {added} swap(s). {missed} skipped (character not in catalog).")

        add_btn.clicked.connect(on_add)
        scale_only_btn.clicked.connect(on_scale_only)
        change_all_btn.clicked.connect(on_change_all)
        remove_btn.clicked.connect(on_remove)
        clear_btn.clicked.connect(on_clear)
        export_btn.clicked.connect(on_export)
        import_btn.clicked.connect(on_import)
        close_btn.clicked.connect(dlg.accept)
        queue_list.itemDoubleClicked.connect(lambda _: on_remove())

        dlg.exec()

    def _field_edit_apply_mesh_swaps(self) -> int:
        queue = self._mesh_swap_queue or []
        if not queue:
            return 0
        if not self._charinfo_data:
            log.warning("mesh swap queue has %d entries but _charinfo_data is empty "
                        "— click 'Load FieldInfo' first", len(queue))
            return 0
        try:
            import crimson_rs
            from character_mesh_swap import apply_mesh_swaps
            game_path = self._config.get("game_install_path", "") or ""
            dp = 'gamedata/binary__/client/bin'
            pabgh = crimson_rs.extract_file(game_path, '0008', dp, 'characterinfo.pabgh')
            pre_bytes = bytes(self._charinfo_data)

            if self._charinfo_original:
                pre_diff_vs_vanilla = sum(1 for a, b in zip(pre_bytes, self._charinfo_original)
                                          if a != b)
                log.info("mesh swap PRE-apply: _charinfo_data has %d bytes "
                         "differing from vanilla (mount cooldown/killable/invincible)",
                         pre_diff_vs_vanilla)

            new_data, applied, report = apply_mesh_swaps(
                pre_bytes, bytes(pabgh), list(queue))

            mesh_diff = sum(1 for a, b in zip(new_data, pre_bytes) if a != b)
            log.info("mesh swap: queue=%d applied=%d diff_bytes=%d",
                     len(queue), applied, mesh_diff)

            if self._charinfo_original:
                post_diff_vs_vanilla = sum(1 for a, b in zip(new_data, self._charinfo_original)
                                           if a != b)
                log.info("mesh swap POST-apply: _charinfo_data has %d bytes "
                         "differing from vanilla (combined: mesh swap + prior edits)",
                         post_diff_vs_vanilla)
                expected_minimum = max(pre_diff_vs_vanilla, mesh_diff)
                if post_diff_vs_vanilla < expected_minimum:
                    log.warning("mesh swap CLOBBER DETECTED: post_diff=%d < expected=%d "
                                "— mesh swap apparently reset some prior bytes!",
                                post_diff_vs_vanilla, expected_minimum)

            if applied:
                self._charinfo_data = bytearray(new_data)
                for r in report:
                    log.info(
                        "mesh swap: %s (%d) <- %s (%d): off=0x%x 0x%08X -> 0x%08X",
                        r['tgt_name'], r['tgt'], r['src_name'], r['src'],
                        r['tgt_offset'], r['old_key'], r['new_key'])
            elif queue:
                log.warning("mesh swap: %d queued swap(s) produced 0 byte changes "
                            "— check character_catalog / parser output", len(queue))

            try:
                needs_overlay = [
                    s for s in queue
                    if float(s.get('scale') or 0) > 0 or s.get('rideable')
                ]
                if needs_overlay:
                    overlay_msg = self._deploy_mount_overlays(game_path, needs_overlay)
                    if overlay_msg:
                        log.info("mount overlay: %s", overlay_msg)
            except Exception:
                log.exception("mount overlay deploy failed")

            return applied
        except Exception:
            log.exception("mesh swap apply failed (queue=%d)", len(queue))
            return 0


    _MOUNT_OVERLAY_GROUP = "0062"

    def _deploy_mount_overlays(self, game_path: str, queue: list[dict]) -> str:
        """Deploy skeleton (rider bone) + appearance (scale) overlays for mesh swaps."""
        import crimson_rs
        import crimson_rs.pack_mod
        from character_mesh_swap import _load_appearance_paths, _guess_xml_path
        from characterinfo_full_parser import parse_all_entries
        from rider_bone_injector import inject_rider_bone, has_rider_bone, \
            _write_scaled_appearance

        gp = Path(game_path)
        catalog = _load_appearance_paths() or []
        dp = 'gamedata/binary__/client/bin'
        pabgh = crimson_rs.extract_file(game_path, '0008', dp, 'characterinfo.pabgh')
        entries = parse_all_entries(bytes(self._charinfo_data), bytes(pabgh))
        # parse_all_entries uses 'name', but _guess_xml_path expects 'internal_name'
        by_key = {}
        for e in entries:
            k = int(e.get('entry_key', 0))
            if 'name' in e and 'internal_name' not in e:
                e['internal_name'] = e['name']
            by_key[k] = e

        files_written = 0
        with tempfile.TemporaryDirectory(prefix='mount_overlay_') as tmp_dir:
            for sw in queue:
                sk = int(sw['src'])
                entry = by_key.get(sk)
                if not entry:
                    log.warning("mount overlay: src=%d not in characterinfo", sk)
                    continue

                # Find the appearance XML path for this character
                xml_path = _guess_xml_path(entry, catalog)

                # ── Scale override ──
                scale = float(sw.get('scale') or 0)
                if scale > 0 and xml_path:
                    try:
                        _write_scaled_appearance(
                            game_path, xml_path, scale, tmp_dir)
                        files_written += 1
                        log.info("mount overlay: scale %.1f for src=%d xml=%s",
                                 scale, sk, xml_path)
                    except Exception:
                        log.exception("mount overlay: scale failed for src=%d", sk)

                # ── Rider bone injection ──
                if sw.get('rideable'):
                    rider_y = float(sw.get('rider_y', 8.0) or 8.0)
                    # Find the skeleton .pab path from the prefabdata
                    skel_path = self._find_skeleton_path(game_path, entry, catalog)
                    if not skel_path:
                        log.warning("mount overlay: can't find skeleton for src=%d", sk)
                        continue
                    try:
                        skel_dir = '/'.join(skel_path.split('/')[:-1])
                        skel_file = skel_path.split('/')[-1]
                        pab_data = bytes(crimson_rs.extract_file(
                            game_path, '0009', skel_dir, skel_file))
                        if has_rider_bone(pab_data):
                            log.info("mount overlay: src=%d already has B_Rider_01", sk)
                        else:
                            modified = inject_rider_bone(pab_data, rider_y=rider_y)
                            out_dir = os.path.join(tmp_dir, *skel_path.split('/')[:-1])
                            os.makedirs(out_dir, exist_ok=True)
                            with open(os.path.join(out_dir, skel_file), 'wb') as f:
                                f.write(modified)
                            files_written += 1
                            log.info("mount overlay: injected B_Rider_01 Y=%.1f "
                                     "for src=%d skel=%s", rider_y, sk, skel_path)
                    except Exception:
                        log.exception("mount overlay: rider inject failed for src=%d", sk)

            if files_written == 0:
                return "no overlay files needed"

            # Pack and deploy
            pack_out = os.path.join(tmp_dir, 'output')
            os.makedirs(pack_out, exist_ok=True)
            group = self._MOUNT_OVERLAY_GROUP
            crimson_rs.pack_mod.pack_mod(
                game_dir=game_path, mod_folder=tmp_dir,
                output_dir=pack_out, group_name=group)

            papgt = gp / 'meta' / '0.papgt'
            backup = papgt.with_suffix(f'.papgt.mount_{group}_bak')
            if papgt.exists() and not backup.exists():
                shutil.copy2(papgt, backup)

            dest = gp / group
            dest.mkdir(exist_ok=True)
            shutil.copyfile(
                os.path.join(pack_out, group, '0.paz'), dest / '0.paz')
            shutil.copyfile(
                os.path.join(pack_out, group, '0.pamt'), dest / '0.pamt')
            shutil.copyfile(
                os.path.join(pack_out, 'meta', '0.papgt'), papgt)

        return f"deployed {files_written} file(s) to {group}/"

    def _find_skeleton_path(self, game_path: str, entry: dict,
                            catalog: list[dict]) -> str | None:
        """Resolve a characterinfo entry to its skeleton .pab PAZ path.

        Strategy: find the appearance XML path, then search the PAMT for
        a .pab skeleton in a matching model/ directory. The appearance path
        encodes the creature family which maps to the skeleton directory.

        Example:
          appearance: character/appearance/2_mon/cd_m0004_00_dragon/cd_m0004_00_golemdragon_0001/...
          skeleton:   character/model/2_mon/cd_m0004_00_dragon/cd_m0004_00_golemdragon/cd_m0004_00_golemdragon.pab
        """
        try:
            import crimson_rs
            from character_mesh_swap import _guess_xml_path

            xml_path = _guess_xml_path(entry, catalog)
            if not xml_path:
                log.warning("_find_skeleton_path: no xml_path for %s",
                            entry.get('internal_name'))
                return None

            # Extract the creature stem from the appearance path DIRECTORIES (not filename)
            # e.g. ".../cd_m0004_00_golemdragon_0001/file.app.xml" -> "cd_m0004_00_golemdragon"
            parts = xml_path.split('/')
            dirs = parts[:-1]  # exclude filename
            creature_stem = None
            for p in reversed(dirs):
                if p.startswith('cd_') and '_00_' in p:
                    # Strip trailing variant number suffix
                    # "cd_m0004_00_golemdragon_0001" -> "cd_m0004_00_golemdragon"
                    segs = p.split('_')
                    while segs and segs[-1].isdigit():
                        segs.pop()
                    creature_stem = '_'.join(segs)
                    break

            if not creature_stem:
                log.warning("_find_skeleton_path: can't extract creature stem from %s",
                            xml_path)
                return None

            # Search PAMT for a .pab skeleton matching this creature
            pamt = crimson_rs.parse_pamt_file(
                os.path.join(game_path, '0009', '0.pamt'))

            target_pab = creature_stem + '.pab'
            for d in pamt.get('directories', []):
                if '/model/' in d['path']:
                    for f in d.get('files', []):
                        if f['name'] == target_pab:
                            result = d['path'] + '/' + f['name']
                            log.info("_find_skeleton_path: %s -> %s",
                                     entry.get('internal_name'), result)
                            return result

            log.warning("_find_skeleton_path: no .pab named %s in PAMT", target_pab)
        except Exception:
            log.exception("_find_skeleton_path failed")
        return None

    def _write_modified_files(self, mod_dir: str) -> None:
        written: list[str] = []
        if (self._field_edit_data and self._field_edit_original
                and bytes(self._field_edit_data) != self._field_edit_original):
            with open(os.path.join(mod_dir, "fieldinfo.pabgb"), "wb") as f:
                f.write(self._field_edit_data)
            written.append(f"fieldinfo.pabgb ({len(self._field_edit_data)}B)")
        if (self._vehicle_data and self._vehicle_original
                and bytes(self._vehicle_data) != self._vehicle_original):
            with open(os.path.join(mod_dir, "vehicleinfo.pabgb"), "wb") as f:
                f.write(self._vehicle_data)
            written.append(f"vehicleinfo.pabgb ({len(self._vehicle_data)}B)")
        if (self._gptrigger_data and self._gptrigger_original
                and bytes(self._gptrigger_data) != self._gptrigger_original):
            with open(os.path.join(mod_dir, "gameplaytrigger.pabgb"), "wb") as f:
                f.write(self._gptrigger_data)
            written.append(f"gameplaytrigger.pabgb ({len(self._gptrigger_data)}B)")
        if (self._regioninfo_data and self._regioninfo_original
                and bytes(self._regioninfo_data) != self._regioninfo_original):
            with open(os.path.join(mod_dir, "regioninfo.pabgb"), "wb") as f:
                f.write(self._regioninfo_data)
            written.append(f"regioninfo.pabgb ({len(self._regioninfo_data)}B)")
        if (self._charinfo_data and self._charinfo_original
                and bytes(self._charinfo_data) != self._charinfo_original):
            with open(os.path.join(mod_dir, "characterinfo.pabgb"), "wb") as f:
                f.write(self._charinfo_data)
            diff_bytes = sum(1 for a, b in zip(self._charinfo_data, self._charinfo_original)
                             if a != b)
            written.append(f"characterinfo.pabgb ({len(self._charinfo_data)}B, "
                           f"{diff_bytes} bytes diff)")
        if (self._wantedinfo_data and self._wantedinfo_original
                and bytes(self._wantedinfo_data) != self._wantedinfo_original):
            with open(os.path.join(mod_dir, "wantedinfo.pabgb"), "wb") as f:
                f.write(self._wantedinfo_data)
            written.append(f"wantedinfo.pabgb ({len(self._wantedinfo_data)}B)")
        if (self._allygroup_data is not None and self._allygroup_original is not None
                and bytes(self._allygroup_data) != self._allygroup_original):
            with open(os.path.join(mod_dir, "allygroupinfo.pabgb"), "wb") as f:
                f.write(self._allygroup_data)
            written.append(f"allygroupinfo.pabgb ({len(self._allygroup_data)}B)")
        if (self._relationinfo_data is not None and self._relationinfo_original is not None
                and bytes(self._relationinfo_data) != self._relationinfo_original):
            with open(os.path.join(mod_dir, "relationinfo.pabgb"), "wb") as f:
                f.write(self._relationinfo_data)
            written.append(f"relationinfo.pabgb ({len(self._relationinfo_data)}B)")
        if (self._factionrelgrp_data is not None and self._factionrelgrp_original is not None
                and bytes(self._factionrelgrp_data) != self._factionrelgrp_original):
            with open(os.path.join(mod_dir, "factionrelationgroup.pabgb"), "wb") as f:
                f.write(self._factionrelgrp_data)
            written.append(f"factionrelationgroup.pabgb ({len(self._factionrelgrp_data)}B)")
        if written:
            log.info("FieldEdit wrote %d file(s) to mod_dir: %s",
                     len(written), ", ".join(written))
        else:
            log.warning("FieldEdit _write_modified_files: no diffs — no files written")

    def _ally_relation_dirty(self) -> bool:
        ag_dirty = (self._allygroup_data is not None
                    and self._allygroup_original is not None
                    and bytes(self._allygroup_data) != self._allygroup_original)
        ri_dirty = (self._relationinfo_data is not None
                    and self._relationinfo_original is not None
                    and bytes(self._relationinfo_data) != self._relationinfo_original)
        fg_dirty = (self._factionrelgrp_data is not None
                    and self._factionrelgrp_original is not None
                    and bytes(self._factionrelgrp_data) != self._factionrelgrp_original)
        return ag_dirty or ri_dirty or fg_dirty

    def _field_edit_apply(self):
        mesh_queue = self._mesh_swap_queue or []
        ally_dirty = self._ally_relation_dirty()
        if (not self._field_edit_data and not ally_dirty
                and not self._field_edit_modified and not mesh_queue):
            QMessageBox.information(self, tr("FieldEdit"), tr("No modifications to apply."))
            return
        game_path = self._config.get("game_install_path", "")
        if not game_path or not os.path.isdir(game_path):
            QMessageBox.critical(self, tr("Game Path"), tr("Game install path not set."))
            return
        reply = QMessageBox.question(
            self, tr("Apply FieldInfo Changes"),
            "Deploy modified game data to the game?\n\n"
            "IMPORTANT: If you already have a FieldEdit mod applied,\n"
            "click Restore FIRST before applying new changes.\n"
            "Applying over an existing mod will crash the game.\n\n"
            "Creates PAZ overlay. Restart game to take effect.\n"
            "Use Restore to undo.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        mesh_applied = self._field_edit_apply_mesh_swaps()
        if mesh_applied:
            self._field_edit_status.setText(f"Applied {mesh_applied} mesh swap(s). Packing...")
        else:
            self._field_edit_status.setText(tr("Packing with pack_mod..."))
        QApplication.processEvents()

        log.info("=== field_edit apply pipeline: dirty-buffer summary ===")
        buffers = [
            ("fieldinfo",     self._field_edit_data,   self._field_edit_original),
            ("vehicleinfo",   self._vehicle_data,      self._vehicle_original),
            ("gameplaytrigger", self._gptrigger_data,  self._gptrigger_original),
            ("regioninfo",    self._regioninfo_data,   self._regioninfo_original),
            ("characterinfo", self._charinfo_data,     self._charinfo_original),
            ("wantedinfo",    self._wantedinfo_data,   self._wantedinfo_original),
            ("allygroupinfo", self._allygroup_data,    self._allygroup_original),
            ("relationinfo",  self._relationinfo_data, self._relationinfo_original),
            ("factionrelationgroup", self._factionrelgrp_data, self._factionrelgrp_original),
        ]
        dirty_names: list[str] = []
        for name, data, orig in buffers:
            if data is None:
                log.info("  %-16s : NOT LOADED", name)
            elif orig is None:
                log.info("  %-16s : no original snapshot (loaded but baseline missing)", name)
            elif bytes(data) == orig:
                log.info("  %-16s : clean (%d B, no changes)", name, len(data))
            else:
                diff = sum(1 for a, b in zip(data, orig) if a != b)
                log.info("  %-16s : DIRTY (%d B, %d bytes diff vs vanilla)",
                         name, len(data), diff)
                dirty_names.append(name)
        if not dirty_names:
            log.warning("field_edit apply: NO buffers are dirty — pack_mod will produce an empty overlay!")
        else:
            log.info("field_edit apply: %d dirty buffer(s) will be packed: %s",
                     len(dirty_names), ", ".join(dirty_names))

        try:
            import crimson_rs.pack_mod
            gp = Path(game_path)
            mod_group = "0039"
            with tempfile.TemporaryDirectory() as tmp_dir:
                mod_dir = os.path.join(tmp_dir, "gamedata", "binary__", "client", "bin")
                os.makedirs(mod_dir, exist_ok=True)
                self._write_modified_files(mod_dir)

                pack_out = os.path.join(tmp_dir, "output")
                os.makedirs(pack_out, exist_ok=True)
                crimson_rs.pack_mod.pack_mod(
                    game_dir=game_path,
                    mod_folder=tmp_dir,
                    output_dir=pack_out,
                    group_name=mod_group,
                )
                try:
                    pamt_path = os.path.join(pack_out, mod_group, "0.pamt")
                    if os.path.isfile(pamt_path):
                        import re as _re
                        with open(pamt_path, "rb") as _f:
                            _pamt = _f.read()
                        _files = sorted(set(
                            m.decode('ascii', errors='replace')
                            for m in _re.findall(rb'[A-Za-z0-9_]+\.pabgb', _pamt)))
                        log.info("field_edit pack_mod verifier: PAMT lists %d file(s): %s",
                                 len(_files), ", ".join(_files) if _files else "(none)")
                except Exception:
                    log.exception("field_edit pack_mod post-verify failed")
                papgt_path = gp / "meta" / "0.papgt"
                backup_path = papgt_path.with_suffix(".papgt.field_bak")
                if papgt_path.exists() and not backup_path.exists():
                    shutil.copy2(papgt_path, backup_path)
                dest = gp / mod_group
                dest.mkdir(exist_ok=True)
                shutil.copyfile(os.path.join(pack_out, mod_group, "0.paz"), dest / "0.paz")
                shutil.copyfile(os.path.join(pack_out, mod_group, "0.pamt"), dest / "0.pamt")
                shutil.copyfile(os.path.join(pack_out, "meta", "0.papgt"), papgt_path)

            self._field_edit_status.setText(f"Applied to {mod_group}/")
            QMessageBox.information(self, tr("Applied"),
                f"FieldInfo mod deployed to {mod_group}/.\n"
                f"Restart the game for changes to take effect.")
        except Exception as e:
            log.exception("FieldEdit apply failed")
            self._field_edit_status.setText(f"Apply failed: {e}")
            QMessageBox.critical(self, tr("Apply Failed"), str(e))

    def _field_edit_export_mod(self):
        mesh_queue = self._mesh_swap_queue or []
        ally_dirty = self._ally_relation_dirty()
        if (not self._field_edit_data and not ally_dirty
                and not self._field_edit_modified and not mesh_queue):
            QMessageBox.information(self, tr("Export as Mod"), tr("No modifications to export."))
            return
        self._field_edit_apply_mesh_swaps()

        name, ok = QInputDialog.getText(self, tr("Export as Mod"),
                                        tr("Mod name:"), text="FieldEdit Mod")
        if not ok or not name.strip():
            return
        name = name.strip()

        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0] or "."))
        default_dir = os.path.join(exe_dir, "packs")
        os.makedirs(default_dir, exist_ok=True)
        folder_name = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in name)
        out_path = os.path.join(default_dir, folder_name)

        try:
            if os.path.isdir(out_path):
                shutil.rmtree(out_path)
            os.makedirs(out_path, exist_ok=True)
            files_dir = os.path.join(out_path, "files", "gamedata", "binary__", "client", "bin")
            os.makedirs(files_dir, exist_ok=True)
            self._write_modified_files(files_dir)

            modinfo = {
                "id": name.lower().replace(" ", "_"),
                "name": name,
                "version": "1.0.0",
                "game_version": "1.00.03",
                "author": "CrimsonSaveEditor",
                "description": f"FieldEdit mod: {name}",
            }
            with open(os.path.join(out_path, "modinfo.json"), "w", encoding="utf-8") as f:
                json.dump(modinfo, f, indent=2)

            written = sorted(os.listdir(files_dir))
            self._field_edit_status.setText(
                f"Exported mod to packs/{folder_name}/ ({len(written)} file(s))")
            QMessageBox.information(self, tr("Mod Exported"),
                f"Mod exported to:\n{out_path}\n\n"
                f"Contents:\n"
                f"  files/gamedata/binary__/client/bin/\n"
                + "".join(f"    {fn}\n" for fn in written)
                + f"  modinfo.json\n\n"
                f"To install: copy '{folder_name}' into your mod loader's\n"
                f"mods/ directory (CD JSON Mod Manager, DMM, or CDUMM).")
        except Exception as e:
            log.exception("FieldEdit raw-mod export failed")
            self._field_edit_status.setText(f"Export failed: {e}")
            QMessageBox.critical(self, tr("Export Failed"), str(e))

    def _field_edit_export(self):
        mesh_queue = self._mesh_swap_queue or []
        ally_dirty = self._ally_relation_dirty()
        if (not self._field_edit_data and not ally_dirty
                and not self._field_edit_modified and not mesh_queue):
            QMessageBox.information(self, tr("FieldEdit"), tr("No modifications to export."))
            return
        self._field_edit_apply_mesh_swaps()

        name, ok = QInputDialog.getText(self, tr("Export Field Mod"),
                                        tr("Mod name:"), text="Mount Everywhere")
        if not ok or not name.strip():
            return
        name = name.strip()

        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        default_dir = os.path.join(exe_dir, "packs")
        os.makedirs(default_dir, exist_ok=True)
        folder_name = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in name)
        save_dir = QFileDialog.getExistingDirectory(
            self, f"Choose folder for '{folder_name}' mod", default_dir)
        if not save_dir:
            return
        out_path = os.path.join(save_dir, folder_name)

        self._field_edit_status.setText(tr("Packing..."))
        QApplication.processEvents()

        try:
            import crimson_rs.pack_mod
            game_path = self._config.get("game_install_path", "")
            if os.path.isdir(out_path):
                shutil.rmtree(out_path)
            os.makedirs(out_path, exist_ok=True)

            with tempfile.TemporaryDirectory() as tmp_dir:
                mod_dir = os.path.join(tmp_dir, "gamedata", "binary__", "client", "bin")
                os.makedirs(mod_dir, exist_ok=True)
                self._write_modified_files(mod_dir)

                pack_out = os.path.join(tmp_dir, "output")
                os.makedirs(pack_out, exist_ok=True)
                mod_group = "0036"
                crimson_rs.pack_mod.pack_mod(
                    game_dir=game_path,
                    mod_folder=tmp_dir,
                    output_dir=pack_out,
                    group_name=mod_group,
                )
                paz_dst = os.path.join(out_path, mod_group)
                os.makedirs(paz_dst, exist_ok=True)
                shutil.copy2(os.path.join(pack_out, mod_group, "0.paz"),
                             os.path.join(paz_dst, "0.paz"))
                shutil.copy2(os.path.join(pack_out, mod_group, "0.pamt"),
                             os.path.join(paz_dst, "0.pamt"))
                meta_dst = os.path.join(out_path, "meta")
                os.makedirs(meta_dst, exist_ok=True)
                shutil.copy2(os.path.join(pack_out, "meta", "0.papgt"),
                             os.path.join(meta_dst, "0.papgt"))

            modinfo = {
                "id": name.lower().replace(" ", "_"),
                "name": name,
                "version": "1.0.0",
                "game_version": "1.00.03",
                "author": "CrimsonSaveEditor",
                "description": f"FieldInfo mod: {name}",
            }
            with open(os.path.join(out_path, "modinfo.json"), "w", encoding="utf-8") as f:
                json.dump(modinfo, f, indent=2)

            self._field_edit_status.setText(f"Exported to {folder_name}/{mod_group}/")
            QMessageBox.information(self, tr("Exported"),
                f"Mod exported to:\n{out_path}\n\n"
                f"Contents:\n"
                f"  {mod_group}/0.paz + {mod_group}/0.pamt\n"
                f"  meta/0.papgt\n"
                f"  modinfo.json\n\n"
                f"JMM / CDUMM should show this as 'compiled'.")
        except Exception as e:
            log.exception("FieldEdit export failed")
            self._field_edit_status.setText(f"Export failed: {e}")
            QMessageBox.critical(self, tr("Export Failed"), str(e))

    def _field_edit_export_json(self):
        self._field_edit_apply_mesh_swaps()
        mesh_queue = self._mesh_swap_queue or []
        if not self._field_edit_modified and not mesh_queue:
            QMessageBox.information(self, tr("Export JSON"), tr("No modifications to export."))
            return

        name, ok = QInputDialog.getText(self, tr("Export JSON Mod"),
                                        tr("Mod name:"), text="Mount Everywhere")
        if not ok or not name.strip():
            return
        name = name.strip()

        def _diff_bytes(data, original, label_fn=None):
            changes = []
            i = 0
            while i < len(data):
                if data[i] != original[i]:
                    start = i
                    while i < len(data) and data[i] != original[i]:
                        i += 1
                    label = label_fn(start, i - start) if label_fn else f"offset_{start}"
                    changes.append({
                        "offset": start,
                        "label": label,
                        "original": bytes(original[start:i]).hex().upper(),
                        "patched": bytes(data[start:i]).hex().upper(),
                    })
                else:
                    i += 1
            return changes

        patches = []

        if self._field_edit_data and self._field_edit_original:
            def _fi_label(off, _n):
                for e in self._field_edit_entries:
                    if e.get('can_call_vehicle_offset') == off:
                        return f"{e.get('name', 'Zone_' + str(e['key']))}: _canCallVehicle ({e.get('can_call_vehicle', '?')})"
                    if e.get('always_call_vehicle_dev_offset') == off:
                        return f"{e.get('name', 'Zone_' + str(e['key']))}: _alwaysCallVehicle_dev ({e.get('always_call_vehicle_dev', '?')})"
                return f"fieldinfo offset {off}"
            ch = _diff_bytes(self._field_edit_data, self._field_edit_original, _fi_label)
            if ch:
                patches.append({"game_file": "gamedata/fieldinfo.pabgb", "changes": ch})

        if self._vehicle_data and self._vehicle_original:
            def _vi_label(off, _n):
                for e in self._vehicle_entries:
                    if e.get('mount_call_type_offset') == off:
                        return f"{e['name']}: _mountCallType ({e.get('mount_call_type', '?')})"
                    if e.get('can_call_safe_zone_offset') == off:
                        return f"{e['name']}: _canCallSafeZone ({e.get('can_call_safe_zone', '?')})"
                    ao = e.get('altitude_cap_offset', -1)
                    if ao <= off < ao + 4:
                        return f"{e['name']}: _altitudeCap"
                return f"vehicleinfo offset {off}"
            ch = _diff_bytes(self._vehicle_data, self._vehicle_original, _vi_label)
            if ch:
                patches.append({"game_file": "gamedata/vehicleinfo.pabgb", "changes": ch})

        if self._gptrigger_data and self._gptrigger_original:
            def _gt_label(off, _n):
                for e in self._gptrigger_entries:
                    if e.get('safe_zone_type_offset') == off:
                        return f"{e['name']}: _safeZoneType ({e.get('safe_zone_type', '?')})"
                return f"gameplaytrigger offset {off}"
            ch = _diff_bytes(self._gptrigger_data, self._gptrigger_original, _gt_label)
            if ch:
                patches.append({"game_file": "gamedata/gameplaytrigger.pabgb", "changes": ch})

        if self._regioninfo_data and self._regioninfo_original:
            ri_field_map = {}
            if self._regioninfo_entries:
                from regioninfo_parser import parse_pabgh_index as ri_idx_fn
                ri_idx = ri_idx_fn(self._regioninfo_schema)
                for e in self._regioninfo_entries:
                    entry_off = ri_idx.get(e['_key'])
                    if entry_off is None:
                        continue
                    rname = e.get('_stringKey', f"Region_{e['_key']}")
                    p = entry_off
                    p += 2
                    slen = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + slen
                    p += 1
                    p += 1; p += 8
                    dslen = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + dslen
                    p += 4
                    rk_c = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + rk_c * 8
                    p += 2
                    cr_c = struct.unpack_from('<I', self._regioninfo_data, p)[0]; p += 4 + cr_c * 2
                    p += 2; p += 4; p += 1; p += 4
                    ri_field_map[p] = f"{rname}: _limitVehicleRun"
                    ri_field_map[p + 1] = f"{rname}: _isTown"
                    ri_field_map[p + 2] = f"{rname}: _isWild"
            def _ri_label(off, _n):
                return ri_field_map.get(off, f"regioninfo offset {off}")
            ch = _diff_bytes(self._regioninfo_data, self._regioninfo_original, _ri_label)
            if ch:
                patches.append({"game_file": "gamedata/regioninfo.pabgb", "changes": ch})

        if self._charinfo_data and self._charinfo_original:
            ci_field_map = {}
            from characterinfo_full_parser import parse_all_entries as _ci_all
            _all_ci = _ci_all(bytes(self._charinfo_data), self._charinfo_schema)
            for e in _all_ci:
                dur_off = e.get('_callMercenarySpawnDuration_offset', -1)
                cool_off = e.get('_callMercenaryCoolTime_offset', -1)
                att_off = e.get('_isAttackable_offset', -1)
                inv_off = e.get('_invincibility_offset', -1)
                nm = e.get('name', '?')
                if dur_off >= 0:
                    ci_field_map[dur_off] = f"{nm}: _callMercenarySpawnDuration"
                if cool_off >= 0:
                    ci_field_map[cool_off] = f"{nm}: _callMercenaryCoolTime"
                if att_off >= 0:
                    ci_field_map[att_off] = f"{nm}: _isAttackable"
                if inv_off >= 0:
                    ci_field_map[inv_off] = f"{nm}: _invincibility"
            def _ci_label(off, _n):
                for foff, lbl in ci_field_map.items():
                    if foff <= off < foff + 8:
                        return lbl
                return f"characterinfo offset {off}"
            ch = _diff_bytes(self._charinfo_data, self._charinfo_original, _ci_label)
            if ch:
                patches.append({"game_file": "gamedata/characterinfo.pabgb", "changes": ch})

        if self._wantedinfo_data and self._wantedinfo_original:
            from wantedinfo_parser import parse_all_entries as wi_parse, FACTION_NAMES, CRIME_TIERS
            wi_entries = wi_parse(bytes(self._wantedinfo_data), self._wantedinfo_schema)
            wi_field_map = {}
            for e in wi_entries:
                faction = FACTION_NAMES.get(e['_faction'], f"Faction_{e['_faction']}")
                tier = CRIME_TIERS.get(e['_crimeTier'], f"Tier_{e['_crimeTier']}")
                off = e.get('_isBlocked_offset', -1)
                if off >= 0:
                    wi_field_map[off] = f"{faction}_{tier}: _isBlocked"
                price_off = e.get('_increasePrice_offset', -1)
                if price_off >= 0:
                    for b in range(8):
                        wi_field_map[price_off + b] = f"{faction}_{tier}: _increasePrice"
            def _wi_label(off, _n):
                return wi_field_map.get(off, f"wantedinfo offset {off}")
            ch = _diff_bytes(self._wantedinfo_data, self._wantedinfo_original, _wi_label)
            if ch:
                patches.append({"game_file": "gamedata/wantedinfo.pabgb", "changes": ch})

        if not patches:
            QMessageBox.information(self, tr("Export JSON"), tr("No byte-level changes detected."))
            return

        total_changes = sum(len(p['changes']) for p in patches)
        export = {
            "name": name,
            "version": "1.0.0",
            "author": "CrimsonSaveEditor",
            "description": f"{name} — {total_changes} changes across {len(patches)} game files.",
            "patches": patches,
        }

        default_name = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in name) + ".json"
        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export JSON Mod"), default_name, "JSON Files (*.json)")
        if not path:
            return
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(export, f, indent=2, ensure_ascii=False)

        self._field_edit_status.setText(f"Exported {total_changes} changes to {os.path.basename(path)}")
        QMessageBox.information(self, tr("Exported"),
            f"Saved {total_changes} changes across {len(patches)} files to:\n{path}")

    def _field_edit_export_mesh_json(self):
        queue = self._mesh_swap_queue or []
        if not queue:
            QMessageBox.information(
                self, tr("Export Mesh Swap as JSON Mod"),
                tr("Mesh swap queue is empty — open Mesh Swap and add some swaps first."))
            return

        if not self._charinfo_data or not self._charinfo_schema:
            game_path = self._config.get("game_install_path", "")
            if not game_path or not os.path.isdir(game_path):
                QMessageBox.warning(
                    self, tr("Game Path"),
                    tr("Game path not set — either click 'Load FieldInfo' first or "
                       "configure the game install path."))
                return
            try:
                import crimson_rs
                dp = "gamedata/binary__/client/bin"
                ci_body = crimson_rs.extract_file(game_path, "0008", dp, "characterinfo.pabgb")
                ci_gh = crimson_rs.extract_file(game_path, "0008", dp, "characterinfo.pabgh")
                ci_pabgb = bytes(ci_body)
                ci_pabgh = bytes(ci_gh)
            except Exception as e:
                log.exception("characterinfo extract for JMM export failed")
                QMessageBox.critical(self, tr("Extract Failed"), str(e))
                return
        else:
            ci_pabgb = bytes(self._charinfo_data)
            ci_pabgh = bytes(self._charinfo_schema)

        try:
            from characterinfo_full_parser import parse_all_entries, parse_pabgh_index
            idx = parse_pabgh_index(ci_pabgh)
            parsed = parse_all_entries(ci_pabgb, ci_pabgh)
        except Exception as e:
            log.exception("characterinfo parse for JMM export failed")
            QMessageBox.critical(self, tr("Parse Failed"), str(e))
            return

        by_key = {}
        for e in parsed:
            ek = e.get('entry_key')
            if ek is not None:
                by_key[int(ek)] = e

        changes = []
        skipped_missing = []
        skipped_no_appearance = []
        for sw in queue:
            try:
                tk = int(sw['tgt'])
                sk = int(sw['src'])
            except (KeyError, TypeError, ValueError):
                continue
            tgt = by_key.get(tk)
            src = by_key.get(sk)
            if tgt is None or src is None:
                skipped_missing.append((tk, sk))
                continue
            tgt_appear_off = tgt.get('_appearanceName_stream_offset')
            tgt_appear_key = tgt.get('_appearanceName_key')
            src_appear_key = src.get('_appearanceName_key')
            if (tgt_appear_off is None or tgt_appear_key is None
                    or src_appear_key is None):
                skipped_no_appearance.append((tk, sk))
                continue
            blob_start = idx.get(tk)
            if blob_start is None:
                skipped_missing.append((tk, sk))
                continue
            rel_offset = tgt_appear_off - blob_start
            entry_name = tgt.get('name') or f"char_{tk}"
            src_name = src.get('name') or f"char_{sk}"
            original_hex = struct.pack('<I', int(tgt_appear_key) & 0xFFFFFFFF).hex()
            patched_hex = struct.pack('<I', int(src_appear_key) & 0xFFFFFFFF).hex()
            changes.append({
                "entry": entry_name,
                "rel_offset": int(rel_offset),
                "original": original_hex,
                "patched": patched_hex,
                "label": (f"Mesh Swap: {entry_name} (target key {tk}) "
                          f"-> looks like {src_name} (source key {sk})"),
            })

        if not changes:
            msg = "No exportable swaps — every queued entry failed a pre-flight check."
            if skipped_missing:
                msg += f"\n\n{len(skipped_missing)} missing from characterinfo.pabgb."
            if skipped_no_appearance:
                msg += f"\n{len(skipped_no_appearance)} missing _appearanceName field."
            QMessageBox.warning(self, tr("Export Mesh Swap as JSON Mod"), msg)
            return

        title, ok = QInputDialog.getText(
            self, tr("Export Mesh Swap as JSON Mod"),
            tr("Mod name:"), text="Mesh Swap Pack")
        if not ok or not title.strip():
            return
        title = title.strip()

        desc_default = f"{len(changes)} character mesh swap(s) via _appearanceName patching."
        description, ok = QInputDialog.getText(
            self, tr("Export Mesh Swap as JSON Mod"),
            tr("Description (optional):"), text=desc_default)
        if not ok:
            return
        description = description.strip() or desc_default

        author, ok = QInputDialog.getText(
            self, tr("Export Mesh Swap as JSON Mod"),
            tr("Author (optional):"), text="CrimsonSaveEditor")
        if not ok:
            return
        author = author.strip() or "CrimsonSaveEditor"

        jmm_mod = {
            "modinfo": {
                "title": title,
                "version": "1.0",
                "description": description,
                "author": author,
            },
            "format": 2,
            "patches": [{
                "game_file": "gamedata/characterinfo.pabgb",
                "changes": changes,
            }],
        }

        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0] or "."))
        default_dir = os.path.join(exe_dir, "packs")
        os.makedirs(default_dir, exist_ok=True)
        safe_name = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in title)
        default_path = os.path.join(default_dir, safe_name + ".json")

        path, _ = QFileDialog.getSaveFileName(
            self, tr("Export Mesh Swap as JSON Mod"),
            default_path, "JSON Files (*.json)")
        if not path:
            return

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(jmm_mod, f, indent=2, ensure_ascii=False)
        except Exception as e:
            log.exception("JMM mesh swap export write failed")
            QMessageBox.critical(self, tr("Export Failed"), str(e))
            return

        warn_suffix = ""
        if skipped_missing or skipped_no_appearance:
            warn_suffix = (f"\n\n{len(skipped_missing)} skipped (not in characterinfo), "
                           f"{len(skipped_no_appearance)} skipped (no _appearanceName).")
        self._field_edit_status.setText(
            f"Exported {len(changes)} mesh swap(s) to {os.path.basename(path)}")
        QMessageBox.information(
            self, tr("Exported"),
            f"Wrote {len(changes)} mesh swap patch(es) to:\n{path}\n\n"
            f"Drop this .json file into JMM's mods/ folder to install."
            f"{warn_suffix}")

    def _field_edit_restore(self):
        game_path = self._config.get("game_install_path", "")
        if not game_path or not os.path.isdir(game_path):
            QMessageBox.warning(self, tr("Game Path"), tr("Game path not set."))
            return
        mod_group = "0039"
        mount_group = self._MOUNT_OVERLAY_GROUP
        game_mod = os.path.join(game_path, mod_group)
        mount_mod = os.path.join(game_path, mount_group)
        has_field = os.path.isdir(game_mod)
        has_mount = os.path.isdir(mount_mod)
        if not has_field and not has_mount:
            QMessageBox.information(self, tr("Restore"),
                f"No {mod_group}/ or {mount_group}/ overlay found.")
            return
        parts = []
        if has_field:
            parts.append(f"{mod_group}/ (field edits + mesh swap)")
        if has_mount:
            parts.append(f"{mount_group}/ (skeleton + scale overlays)")
        reply = QMessageBox.question(
            self, tr("Restore Vanilla FieldInfo"),
            f"Remove overlays and restore vanilla?\n\n" + "\n".join(parts),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        try:
            msgs = []
            if has_field:
                if self._rebuild_papgt_fn:
                    msgs.append(self._rebuild_papgt_fn(game_path, mod_group))
                shutil.rmtree(game_mod)
            if has_mount:
                if self._rebuild_papgt_fn:
                    msgs.append(self._rebuild_papgt_fn(game_path, mount_group))
                shutil.rmtree(mount_mod)
            self._field_edit_status.setText(tr("Restored vanilla fieldinfo"))
            QMessageBox.information(self, tr("Restored"),
                f"Removed overlays.\n" + "\n".join(msgs) + "\n"
                f"Restart the game for changes to take effect.")
        except Exception as e:
            QMessageBox.critical(self, tr("Restore Failed"), str(e))
