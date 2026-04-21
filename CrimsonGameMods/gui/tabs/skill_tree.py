"""SkillTree Editor tab — cross-character skill / moveset swapping.

Modifies skilltreeinfo.pabgb root package IDs so one character can
use another character's melee moveset. Deploys as PAZ overlay to
group 0063.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui.theme import COLORS

log = logging.getLogger(__name__)

OVERLAY_GROUP = "0063"
INTERNAL_DIR = "gamedata/binary__/client/bin"


class SkillTreeTab(QWidget):
    """Tab for viewing and swapping skill tree root packages."""

    status_message = Signal(str)
    config_save_requested = Signal()

    def __init__(
        self,
        config: dict,
        rebuild_papgt_fn: Optional[Callable[[str, str], str]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._rebuild_papgt_fn = rebuild_papgt_fn
        self._game_path: str = ""

        # Parser state — skilltreeinfo
        self._records: list = []
        self._original_pabgh: bytes = b""
        self._original_pabgb: bytes = b""
        # Parser state — skilltreegroupinfo
        self._group_records: list = []
        self._original_grp_pabgh: bytes = b""
        self._original_grp_pabgb: bytes = b""
        self._loaded = False

        self._build_ui()

    # -- public --------------------------------------------------------

    def set_game_path(self, path: str) -> None:
        self._game_path = path

    # -- UI construction -----------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        # --- top row: Extract + Apply + Restore ---
        top_row = QHBoxLayout()
        self._btn_extract = QPushButton("Extract from Game")
        self._btn_extract.clicked.connect(self._on_extract)
        top_row.addWidget(self._btn_extract)

        top_row.addStretch()

        self._btn_apply = QPushButton("Apply to Game")
        self._btn_apply.setStyleSheet(
            f"background-color: {COLORS['accent']}; color: white; "
            f"font-weight: bold; padding: 6px 16px;"
        )
        self._btn_apply.clicked.connect(self._on_apply)
        self._btn_apply.setEnabled(False)
        top_row.addWidget(self._btn_apply)

        self._btn_restore = QPushButton("Restore")
        self._btn_restore.clicked.connect(self._on_restore)
        top_row.addWidget(self._btn_restore)

        root.addLayout(top_row)

        # --- preset buttons by category ---
        self._preset_btns: list[QPushButton] = []

        # Kliff row
        kliff_row = QHBoxLayout()
        kliff_row.setSpacing(4)
        lbl = QLabel("Kliff:")
        lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        kliff_row.addWidget(lbl)
        kliff_row.addWidget(self._make_preset_btn(
            "Damiane Skills", "#7B1FA2",
            "Give Kliff Damiane's full skill tree + moveset.\n\n"
            "Redirects Kliff's skill tree to load Damiane's tree data.\n"
            "Kliff sees Damiane skills (Marksmanship, Rapier, Pistol, etc.)\n"
            "and uses her combat animations.\n\n"
            "Weapon trees: Sword/Shield/Bow/Spear -> Rapier/Pistol/Longsword",
            {50: 0x332D},
        ))
        kliff_row.addWidget(self._make_preset_btn(
            "Oongka Skills", "#E65100",
            "Give Kliff Oongka's full skill tree + moveset.\n\n"
            "Redirects Kliff's skill tree to load Oongka's tree data.\n"
            "Kliff sees Oongka skills (Greataxe, Blaster, Axe, etc.)\n"
            "and uses his combat animations.\n\n"
            "Weapon trees: Sword/Shield/Bow/Spear -> Greataxe/Blaster/Axe",
            {50: 0x3391},
        ))
        kliff_row.addStretch()
        root.addLayout(kliff_row)

        # Damiane row
        dami_row = QHBoxLayout()
        dami_row.setSpacing(4)
        lbl = QLabel("Damiane:")
        lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        dami_row.addWidget(lbl)
        dami_row.addWidget(self._make_preset_btn(
            "Kliff Skills", "#1565C0",
            "Give Damiane Kliff's full skill tree + moveset.\n\n"
            "Redirects Damiane's skill tree to load Kliff's tree data.\n"
            "Damiane sees Kliff skills (Sword, Shield, Bow, Spear, etc.)\n"
            "and uses his combat animations.\n\n"
            "Weapon trees: Rapier/Pistol/Longsword -> Sword/Shield/Bow/Spear",
            {52: 0x32C9},
        ))
        dami_row.addWidget(self._make_preset_btn(
            "Oongka Skills", "#E65100",
            "Give Damiane Oongka's full skill tree + moveset.\n\n"
            "Redirects Damiane's skill tree to load Oongka's tree data.\n"
            "Damiane sees Oongka skills (Greataxe, Blaster, Axe, etc.)\n"
            "and uses his combat animations.\n\n"
            "Weapon trees: Rapier/Pistol/Longsword -> Greataxe/Blaster/Axe",
            {52: 0x3391},
        ))
        dami_row.addStretch()
        root.addLayout(dami_row)

        # Oongka row
        oong_row = QHBoxLayout()
        oong_row.setSpacing(4)
        lbl = QLabel("Oongka:")
        lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        oong_row.addWidget(lbl)
        oong_row.addWidget(self._make_preset_btn(
            "Kliff Skills", "#1565C0",
            "Give Oongka Kliff's full skill tree + moveset.\n\n"
            "Redirects Oongka's skill tree to load Kliff's tree data.\n"
            "Oongka sees Kliff skills (Sword, Shield, Bow, Spear, etc.)\n"
            "and uses his combat animations.\n\n"
            "Weapon trees: Greataxe/Blaster/Axe -> Sword/Shield/Bow/Spear",
            {51: 0x32C9},
        ))
        oong_row.addWidget(self._make_preset_btn(
            "Damiane Skills", "#7B1FA2",
            "Give Oongka Damiane's full skill tree + moveset.\n\n"
            "Redirects Oongka's skill tree to load Damiane's tree data.\n"
            "Oongka sees Damiane skills (Marksmanship, Rapier, Pistol, etc.)\n"
            "and uses her combat animations.\n\n"
            "Weapon trees: Greataxe/Blaster/Axe -> Rapier/Pistol/Longsword",
            {51: 0x332D},
        ))
        oong_row.addStretch()
        root.addLayout(oong_row)

        # All row
        all_row = QHBoxLayout()
        all_row.setSpacing(4)
        lbl = QLabel("All:")
        lbl.setStyleSheet(f"color: {COLORS['accent']}; font-weight: bold;")
        all_row.addWidget(lbl)
        all_row.addWidget(self._make_preset_btn(
            "Share Kliff", "#1565C0",
            "All 3 characters use Kliff's full skill tree + moveset.\n\n"
            "Redirects Oongka and Damiane to load Kliff's trees.\n"
            "Everyone sees Kliff skills and uses Sword/Shield/Bow/Spear.",
            {50: 0x32C9, 51: 0x32C9, 52: 0x32C9},
        ))
        all_row.addWidget(self._make_preset_btn(
            "Share Damiane", "#7B1FA2",
            "All 3 characters use Damiane's full skill tree + moveset.\n\n"
            "Redirects Kliff and Oongka to load Damiane's trees.\n"
            "Everyone sees Damiane skills and uses Rapier/Pistol/Longsword.",
            {50: 0x332D, 51: 0x332D, 52: 0x332D},
        ))
        all_row.addWidget(self._make_preset_btn(
            "Share Oongka", "#E65100",
            "All 3 characters use Oongka's full skill tree + moveset.\n\n"
            "Redirects Kliff and Damiane to load Oongka's trees.\n"
            "Everyone sees Oongka skills and uses Greataxe/Blaster/Axe.",
            {50: 0x3391, 51: 0x3391, 52: 0x3391},
        ))
        all_row.addWidget(self._make_preset_btn(
            "Reset Vanilla", "#424242",
            "Reset all 3 characters to their original skill trees.\n\n"
            "Restores vanilla group mappings and root packages.\n"
            "Undoes any preset selection (still need to Apply to Game).",
            None,  # special: reset to vanilla
        ))
        all_row.addStretch()
        root.addLayout(all_row)

        # --- table ---
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels([
            "Key", "Name", "Character", "Category", "Size", "Melee Root",
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        root.addWidget(self._table)

        # --- status ---
        self._lbl_status = QLabel("")
        root.addWidget(self._lbl_status)

    def _make_preset_btn(
        self, label: str, color: str, tooltip: str,
        swaps: Optional[dict[int, int]],
    ) -> QPushButton:
        """Create a styled preset button with hover tooltip."""
        btn = QPushButton(label)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"font-weight: bold; padding: 4px 10px;"
        )
        btn.setEnabled(False)
        btn.clicked.connect(
            lambda checked=False, s=swaps: self._apply_preset(s)
        )
        self._preset_btns.append(btn)
        return btn

    # -- extract -------------------------------------------------------

    def _on_extract(self) -> None:
        game_path = self._game_path or self._config.get("game_install_path", "")
        if not game_path:
            QMessageBox.warning(self, "No game path",
                                "Set the game install path in the Patches tab first.")
            return

        try:
            import crimson_rs
            dp = INTERNAL_DIR
            pabgb = crimson_rs.extract_file(game_path, "0008", dp,
                                            "skilltreeinfo.pabgb")
            pabgh = crimson_rs.extract_file(game_path, "0008", dp,
                                            "skilltreeinfo.pabgh")
            grp_gb = crimson_rs.extract_file(game_path, "0008", dp,
                                             "skilltreegroupinfo.pabgb")
            grp_gh = crimson_rs.extract_file(game_path, "0008", dp,
                                             "skilltreegroupinfo.pabgh")
        except Exception as e:
            QMessageBox.critical(self, "Extract failed", str(e))
            return

        self._original_pabgh = bytes(pabgh)
        self._original_pabgb = bytes(pabgb)
        self._original_grp_pabgh = bytes(grp_gh)
        self._original_grp_pabgb = bytes(grp_gb)

        from skilltreeinfo_parser import parse_all, parse_groups
        self._records = parse_all(self._original_pabgh, self._original_pabgb)
        self._group_records = parse_groups(
            self._original_grp_pabgh, self._original_grp_pabgb
        )
        self._loaded = True
        self._populate_table()
        for btn in self._preset_btns:
            btn.setEnabled(True)
        self._btn_apply.setEnabled(True)
        self.status_message.emit(
            f"Loaded {len(self._records)} skill tree entries "
            f"({len(self._original_pabgb)} bytes)"
        )

    def _populate_table(self) -> None:
        from skilltreeinfo_parser import ROOT_PACKAGES, CHAR_MELEE_ROOT

        self._table.setRowCount(len(self._records))
        self._root_combos: dict[int, QComboBox] = {}

        pkg_labels = {v: k for k, v in ROOT_PACKAGES.items()}

        for row, rec in enumerate(self._records):
            # Key
            item_key = QTableWidgetItem(str(rec.key))
            item_key.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, item_key)

            # Name -- display localized name with internal name in tooltip
            item_name = QTableWidgetItem(rec.display_name)
            item_name.setToolTip(rec.name)
            self._table.setItem(row, 1, item_name)

            # Character
            item_char = QTableWidgetItem(rec.character)
            item_char.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, item_char)

            # Category
            item_cat = QTableWidgetItem(rec.category)
            item_cat.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, item_cat)

            # Size
            item_size = QTableWidgetItem(f"{len(rec.to_bytes())}B")
            item_size.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                       Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(row, 4, item_size)

            # Melee Root -- combo for main trees, text for others
            pkgs = rec.find_root_packages()
            if rec.is_main_tree and pkgs:
                combo = QComboBox()
                for label, pkg_id in ROOT_PACKAGES.items():
                    combo.addItem(f"{label} (0x{pkg_id:04X})", pkg_id)
                # Set current to whatever the record has
                current_root = pkgs[0][1]
                for i in range(combo.count()):
                    if combo.itemData(i) == current_root:
                        combo.setCurrentIndex(i)
                        break
                self._table.setCellWidget(row, 5, combo)
                self._root_combos[rec.key] = combo
            elif pkgs:
                labels = [f"{pkg_labels.get(v, '?')} @0x{o:X}" for o, v in pkgs]
                self._table.setItem(row, 5,
                                    QTableWidgetItem("; ".join(labels)))
            else:
                self._table.setItem(row, 5, QTableWidgetItem("--"))

        self._table.resizeColumnsToContents()
        # Re-stretch name and root columns
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

    # -- presets -------------------------------------------------------

    def _apply_preset(self, swaps: Optional[dict[int, int]]) -> None:
        """Apply a preset by updating the table combo boxes."""
        if not self._loaded:
            return
        from skilltreeinfo_parser import CHAR_MELEE_ROOT

        if swaps is None:
            # Reset to vanilla
            swaps = dict(CHAR_MELEE_ROOT)

        for key, new_root in swaps.items():
            if key in self._root_combos:
                combo = self._root_combos[key]
                for i in range(combo.count()):
                    if combo.itemData(i) == new_root:
                        combo.setCurrentIndex(i)
                        break

    # -- apply to game -------------------------------------------------

    def _on_apply(self) -> None:
        if not self._loaded:
            QMessageBox.warning(self, "Not loaded",
                                "Extract skill tree data first.")
            return

        game_path = self._game_path or self._config.get("game_install_path", "")
        if not game_path:
            QMessageBox.warning(self, "No game path",
                                "Set the game install path first.")
            return

        try:
            self._apply_to_game(game_path)
        except Exception as e:
            log.exception("SkillTree apply failed")
            QMessageBox.critical(self, "Apply failed", str(e))

    def _apply_to_game(self, game_path: str) -> None:
        import crimson_rs
        from skilltreeinfo_parser import (
            CHAR_MELEE_ROOT, VANILLA_GROUP_KEYS,
            parse_all, serialize_all, parse_groups, serialize_groups,
        )

        # Re-parse from originals to get clean state
        records = parse_all(self._original_pabgh, self._original_pabgb)
        groups = parse_groups(self._original_grp_pabgh, self._original_grp_pabgb)

        any_change = False
        changes: list[str] = []

        # --- Apply root package combo selections (skilltreeinfo) ---
        for rec in records:
            if rec.key not in self._root_combos:
                continue
            combo = self._root_combos[rec.key]
            new_root = combo.currentData()
            native_root = CHAR_MELEE_ROOT.get(rec.key)
            if native_root is None:
                continue
            if new_root != native_root:
                count = rec.patch_root_package(native_root, new_root)
                if count > 0:
                    any_change = True
                    changes.append(
                        f"{rec.name}: root 0x{native_root:04X} -> "
                        f"0x{new_root:04X} ({count} refs)"
                    )

        # --- Apply group key redirects (skilltreegroupinfo) ---
        # Detect which main tree combos point to a different character
        # and redirect the corresponding group
        main_key_to_char = {50: "Kliff", 51: "Oongka", 52: "Damiane"}
        char_to_main_key = {"Kliff": 50, "Oongka": 51, "Damiane": 52}
        char_to_weapon_keys = {
            "Kliff": [1, 2, 3, 4],
            "Oongka": [11, 12, 13],
            "Damiane": [21, 22, 23],
        }
        char_to_main_grp = {
            "Kliff": 1000000, "Oongka": 1000001, "Damiane": 1000002,
        }
        char_to_wpn_grp = {
            "Kliff": 1000007, "Oongka": 1000011, "Damiane": 1000014,
        }

        for rec_key, combo in self._root_combos.items():
            new_root = combo.currentData()
            native_root = CHAR_MELEE_ROOT.get(rec_key)
            if native_root is None or new_root == native_root:
                continue

            # Figure out which character this record belongs to and
            # which character's tree we're swapping in
            owner_char = main_key_to_char.get(rec_key)
            source_char = None
            for ch, root in CHAR_MELEE_ROOT.items():
                if root == new_root:
                    source_char = main_key_to_char.get(ch)
                    break
            if not owner_char or not source_char:
                continue

            # Redirect main skill group
            main_grp_key = char_to_main_grp[owner_char]
            source_main_key = char_to_main_key[source_char]
            for grp in groups:
                if grp.key == main_grp_key:
                    vanilla = VANILLA_GROUP_KEYS.get(main_grp_key, grp.tree_keys)
                    if grp.tree_keys != [source_main_key]:
                        grp.tree_keys = [source_main_key]
                        any_change = True
                        changes.append(
                            f"{grp.name}: tree keys "
                            f"{vanilla} -> [{source_main_key}]"
                        )
                    break

            # Redirect weapon skill group
            wpn_grp_key = char_to_wpn_grp[owner_char]
            source_wpn_keys = char_to_weapon_keys[source_char]
            for grp in groups:
                if grp.key == wpn_grp_key:
                    vanilla = VANILLA_GROUP_KEYS.get(wpn_grp_key, grp.tree_keys)
                    if grp.tree_keys != source_wpn_keys:
                        grp.tree_keys = list(source_wpn_keys)
                        any_change = True
                        changes.append(
                            f"{grp.name}: tree keys "
                            f"{vanilla} -> {source_wpn_keys}"
                        )
                    break

        if not any_change:
            QMessageBox.information(self, "No changes",
                                    "All trees are at their vanilla values.\n"
                                    "Nothing to deploy.")
            return

        # Serialize both files
        new_pabgh, new_pabgb = serialize_all(records)
        new_grp_gh, new_grp_gb = serialize_groups(groups)

        # Build overlay with PackGroupBuilder(NONE)
        with tempfile.TemporaryDirectory() as tmp_dir:
            group_dir = os.path.join(tmp_dir, OVERLAY_GROUP)
            os.makedirs(group_dir, exist_ok=True)

            builder = crimson_rs.PackGroupBuilder(
                group_dir,
                crimson_rs.Compression.NONE,
                crimson_rs.Crypto.NONE,
            )
            builder.add_file(INTERNAL_DIR, "skilltreeinfo.pabgb", new_pabgb)
            builder.add_file(INTERNAL_DIR, "skilltreeinfo.pabgh", new_pabgh)
            builder.add_file(INTERNAL_DIR, "skilltreegroupinfo.pabgb", new_grp_gb)
            builder.add_file(INTERNAL_DIR, "skilltreegroupinfo.pabgh", new_grp_gh)
            pamt_bytes = bytes(builder.finish())

            # Get PAMT self-reported checksum
            pamt_checksum = crimson_rs.parse_pamt_bytes(pamt_bytes)[
                "checksum"
            ]

            # Deploy files to game directory
            game_mod = os.path.join(game_path, OVERLAY_GROUP)
            if os.path.isdir(game_mod):
                shutil.rmtree(game_mod)
            os.makedirs(game_mod, exist_ok=True)

            shutil.copy2(
                os.path.join(group_dir, "0.paz"),
                os.path.join(game_mod, "0.paz"),
            )
            shutil.copy2(
                os.path.join(group_dir, "0.pamt"),
                os.path.join(game_mod, "0.pamt"),
            )

        # Update PAPGT -- read CURRENT, dedupe, add our entry
        papgt_path = os.path.join(game_path, "meta", "0.papgt")
        papgt = crimson_rs.parse_papgt_file(papgt_path)
        papgt["entries"] = [
            e for e in papgt["entries"]
            if e.get("group_name") != OVERLAY_GROUP
        ]
        papgt = crimson_rs.add_papgt_entry(
            papgt, OVERLAY_GROUP, pamt_checksum, 0, 16383
        )
        crimson_rs.write_papgt_file(papgt, papgt_path)

        # Write marker file
        with open(os.path.join(game_mod, ".se_skilltree"), "w") as f:
            f.write("Created by CrimsonGameMods SkillTree tab\n")
            for c in changes:
                f.write(f"  {c}\n")

        summary = "\n".join(changes)
        self._lbl_status.setText(f"Deployed to {OVERLAY_GROUP}/")
        self.status_message.emit(
            f"SkillTree overlay deployed to {OVERLAY_GROUP}/ "
            f"({len(changes)} swap(s))"
        )
        QMessageBox.information(
            self, "Deployed",
            f"Skill tree overlay deployed to {OVERLAY_GROUP}/\n\n"
            f"{summary}\n\n"
            f"Restart the game to apply changes.",
        )

    # -- restore -------------------------------------------------------

    def _on_restore(self) -> None:
        game_path = self._game_path or self._config.get("game_install_path", "")
        if not game_path:
            QMessageBox.warning(self, "No game path",
                                "Set the game install path first.")
            return

        game_mod = os.path.join(game_path, OVERLAY_GROUP)
        if not os.path.isdir(game_mod):
            QMessageBox.information(self, "Nothing to restore",
                                    f"No {OVERLAY_GROUP}/ overlay found.")
            return

        try:
            # Remove PAPGT entry first
            if self._rebuild_papgt_fn:
                msg = self._rebuild_papgt_fn(game_path, OVERLAY_GROUP)
                log.info("PAPGT restore: %s", msg)

            # Remove overlay directory
            shutil.rmtree(game_mod)

            self._lbl_status.setText("Restored -- overlay removed")
            self.status_message.emit(
                f"SkillTree overlay {OVERLAY_GROUP}/ removed"
            )
            QMessageBox.information(
                self, "Restored",
                f"Removed {OVERLAY_GROUP}/ overlay.\n"
                f"Restart the game to revert to vanilla skill trees.",
            )
        except Exception as e:
            log.exception("SkillTree restore failed")
            QMessageBox.critical(self, "Restore failed", str(e))
