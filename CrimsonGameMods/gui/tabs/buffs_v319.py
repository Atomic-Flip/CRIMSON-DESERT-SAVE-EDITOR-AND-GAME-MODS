"""
Item Buffs tab: ItemBuffsTab — STANDALONE v3.1.9 PORT.

This module is a verbatim paste of the v3.1.9 gui.py ItemBuffs tab,
wrapped in a QWidget subclass with Signal-based bridges to MainWindow.

DO NOT replace method bodies with merged-build variants. If a helper
is missing, add a Signal or a module-level helper — do not rewrite logic.
"""
from __future__ import annotations

import ctypes
import datetime
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback
from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QAction, QBrush, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSizePolicy, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from gui.theme import COLORS, CATEGORY_COLORS
from models import SaveItem, SaveData, UndoEntry
from item_db import ItemNameDB
from equipment_sets import SetManager, EquipmentSet, SetItem, StatOperation
from paz_patcher import (
    PazPatchManager, PazPatch,
    ItemBuffPatcher, ItemRecord, StatTriplet, BUFF_HASHES, BUFF_NAMES,
    ItemEffectPatcher,
)
from icon_cache import IconCache, ICON_SIZE

try:
    from gui.utils import make_help_btn
except Exception:
    def make_help_btn(topic, fn=None):
        btn = QPushButton("?")
        btn.setFixedSize(22, 22)
        if fn:
            btn.clicked.connect(lambda: fn(topic))
        return btn

log = logging.getLogger(__name__)


def _is_admin() -> bool:
    """Check if we're running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _is_game_running() -> bool:
    """Return True if CrimsonDesert.exe is running."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq CrimsonDesert.exe", "/FO", "CSV", "/NH"],
            stderr=subprocess.DEVNULL, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return "CrimsonDesert.exe" in out
    except Exception:
        return False


class ItemBuffsTab(QWidget):
    """Item buff/stat/passive editor — standalone v3.1.9 port."""

    dirty = Signal()
    status_message = Signal(str)
    config_save_requested = Signal()
    paz_refresh_requested = Signal()
    undo_entry_added = Signal(object)
    scan_requested = Signal()
    navigate_requested = Signal(str)

    def __init__(
        self,
        name_db: Optional[ItemNameDB] = None,
        icon_cache=None,
        config: Optional[dict] = None,
        show_guide_fn=None,
        paz_manager: Optional["PazPatchManager"] = None,
        set_manager: Optional["SetManager"] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._name_db = name_db
        self._icon_cache = icon_cache
        self._config = config if config is not None else {}
        self._show_guide_fn = show_guide_fn
        self._paz_manager = paz_manager
        self._set_manager = set_manager if set_manager is not None else SetManager()
        self._save_data: Optional[SaveData] = None
        self._items: List[SaveItem] = []
        self._game_path: str = self._config.get("game_install_path", "")
        self._buff_patcher: Optional[ItemBuffPatcher] = None
        self._buff_rust_lookup = {}
        self._buff_icons_enabled = True
        self._buff_modified = False
        self._buff_item_limits = {}
        self._experimental_mode: bool = bool(self._config.get("experimental_mode", False))
        self._build_ui()

    def set_experimental_mode(self, enabled: bool) -> None:
        self._experimental_mode = bool(enabled)
        if hasattr(self, "_eb_socket_row_widget"):
            self._eb_socket_row_widget.setVisible(self._experimental_mode)
        if hasattr(self, "_buff_apply_game_btn"):
            self._buff_apply_game_btn.setVisible(self._experimental_mode)


    def load(self, save_data: SaveData, items: List[SaveItem]) -> None:
        self._save_data = save_data
        self._items = items if items is not None else []

    def unload(self) -> None:
        self._save_data = None
        self._items = []

    def set_game_path(self, path: str) -> None:
        self._game_path = path or ""
        if hasattr(self, "_buff_game_path") and self._buff_game_path is not None:
            self._buff_game_path.setText(self._game_path)
            self._buff_game_path.setToolTip(self._game_path)

    def set_icons_enabled(self, enabled: bool) -> None:
        if hasattr(self, "_buff_icons_enabled") and self._buff_icons_enabled != enabled:
            self._buff_toggle_icons()


    def _build_ui(self) -> None:
        """Build the ItemBuffs tab — custom stat/buff injection into game data."""
        from PySide6.QtWidgets import QScrollArea, QSizePolicy

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        help_row = QHBoxLayout()
        help_row.setSpacing(4)
        help_row.addStretch(1)
        help_row.addWidget(make_help_btn("itembuffs", self._show_guide_fn))
        layout.addLayout(help_row)

        warn_label = QLabel(
            "\u26A0  Buff and stat names may be inaccurate — they are community-mapped, "
            "not from official game data. Some buffs share numeric keys across different "
            "systems (stats/buffs/passives are 3 separate ID namespaces). "
            "If a name looks wrong, trust the in-game tooltip after applying."
        )
        warn_label.setWordWrap(True)
        warn_label.setStyleSheet(
            f"color: #FFB74D; padding: 6px; font-size: 10px; "
            f"border: 1px solid #5D4037; border-radius: 4px; "
            f"background-color: rgba(93,64,55,0.25);"
        )
        layout.addWidget(warn_label)

        self._buff_path_widget = QWidget()
        self._buff_path_widget.setVisible(False)
        self._buff_game_path = None

        action_row = QHBoxLayout()
        action_row.setSpacing(4)

        extract_rust_btn = QPushButton("Extract")
        extract_rust_btn.setObjectName("accentBtn")
        extract_rust_btn.setToolTip("Extract iteminfo from game using Rust parser")
        extract_rust_btn.clicked.connect(self._buff_extract_rust)
        action_row.addWidget(extract_rust_btn)

        desc_search_btn = QPushButton("Search by Description")
        desc_search_btn.setToolTip(
            "Search ALL buffs, passives, stats by in-game description.\n"
            "Keywords: 'imbue fire', 'damage', 'stamina', 'immunity', etc.")
        desc_search_btn.clicked.connect(self._buff_open_desc_search)
        action_row.addWidget(desc_search_btn)

        adv_json_btn = QPushButton("JSON Edit")
        adv_json_btn.setToolTip(
            "Open raw enchant data as editable JSON — full control")
        adv_json_btn.clicked.connect(self._eb_json_edit)
        action_row.addWidget(adv_json_btn)

        transmog_btn = QPushButton("Transmog / Visual Swap")
        transmog_btn.setStyleSheet("background-color: #6A1B9A; color: white; font-weight: bold;")
        transmog_btn.setToolTip(
            "Swap armor visuals between items.\n"
            "Applied automatically when you Export as Mod or Apply to Game\n"
            "— stacks with all your other ItemBuffs edits.")
        transmog_btn.clicked.connect(self._buff_open_transmog_dialog)
        action_row.addWidget(transmog_btn)


        action_row.addStretch()
        layout.addLayout(action_row)

        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(QLabel("Search:"))
        self._buff_search = QLineEdit()
        self._buff_search.setPlaceholderText("Item name (e.g. Earring, Sword, Necklace)...")
        self._buff_search.returnPressed.connect(self._buff_search_items)
        search_row.addWidget(self._buff_search, 1)

        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._buff_search_items)
        search_row.addWidget(search_btn)

        my_inv_btn = QPushButton("My Inventory")
        my_inv_btn.setToolTip("Show only items from your loaded save that exist in iteminfo")
        my_inv_btn.clicked.connect(self._buff_show_my_inventory)
        search_row.addWidget(my_inv_btn)

        self._buff_show_icons_btn = QPushButton("Icons")
        self._buff_show_icons_btn.setToolTip("Toggle item icons in the items list")
        self._buff_show_icons_btn.clicked.connect(self._buff_toggle_icons)
        search_row.addWidget(self._buff_show_icons_btn)
        self._buff_icons_enabled = False

        layout.addLayout(search_row)

        buff_splitter = QSplitter(Qt.Horizontal)
        buff_splitter.setChildrenCollapsible(False)

        items_frame = QFrame()
        items_vlayout = QVBoxLayout(items_frame)
        items_vlayout.setContentsMargins(0, 0, 0, 0)
        items_vlayout.setSpacing(2)
        items_vlayout.addWidget(QLabel("Matching Items:"))
        self._buff_items_table = QTableWidget()
        self._buff_items_table.setColumnCount(6)
        self._buff_items_table.setHorizontalHeaderLabels(["", "Name", "Type", "Tier", "Enchants", "Stack"])
        self._buff_items_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._buff_items_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._buff_items_table.setSelectionMode(QAbstractItemView.SingleSelection)
        hdr_items = self._buff_items_table.horizontalHeader()
        hdr_items.setSectionResizeMode(0, QHeaderView.Interactive)
        self._buff_items_table.setColumnWidth(0, 0)
        hdr_items.setSectionResizeMode(1, QHeaderView.Interactive)
        self._buff_items_table.setColumnWidth(1, 180)
        hdr_items.setSectionResizeMode(2, QHeaderView.Interactive)
        self._buff_items_table.setColumnWidth(2, 70)
        hdr_items.setSectionResizeMode(3, QHeaderView.Interactive)
        self._buff_items_table.setColumnWidth(3, 50)
        hdr_items.setSectionResizeMode(4, QHeaderView.Interactive)
        self._buff_items_table.setColumnWidth(4, 70)
        hdr_items.setSectionResizeMode(5, QHeaderView.Interactive)
        self._buff_items_table.setColumnWidth(5, 50)
        hdr_items.setStretchLastSection(False)
        self._buff_items_table.verticalHeader().setDefaultSectionSize(24)
        self._buff_items_table.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self._buff_items_table.setSortingEnabled(True)
        self._buff_items_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._buff_items_table.customContextMenuRequested.connect(self._buff_items_context_menu)
        self._buff_items_table.selectionModel().selectionChanged.connect(
            self._buff_item_selected
        )
        self._buff_items_table.setMinimumHeight(120)
        self._buff_items_table.setColumnHidden(3, True)
        self._buff_items_table.setColumnHidden(4, True)
        self._buff_items_table.setColumnHidden(5, True)
        items_vlayout.addWidget(self._buff_items_table, 1)
        items_frame.setMinimumWidth(120)
        items_frame.setMaximumWidth(280)
        buff_splitter.addWidget(items_frame)

        stats_outer = QSplitter(Qt.Vertical)
        stats_outer.setChildrenCollapsible(False)

        stats_table_frame = QFrame()
        stf_layout = QVBoxLayout(stats_table_frame)
        stf_layout.setContentsMargins(0, 0, 0, 0)
        stf_layout.setSpacing(2)
        self._buff_selected_label = QLabel("No item selected — search and click an item on the left")
        self._buff_selected_label.setStyleSheet(
            f"color: {COLORS['text_dim']}; font-weight: bold; padding: 2px 4px;"
        )
        stf_layout.addWidget(self._buff_selected_label)
        stf_layout.addWidget(QLabel("Current Stats / Buffs:"))
        self._buff_stats_table = QTableWidget()
        self._buff_stats_table.setColumnCount(2)
        self._buff_stats_table.setHorizontalHeaderLabels([
            "Stat/Buff", "Value",
        ])
        self._buff_stats_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._buff_stats_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._buff_stats_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._buff_stats_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._buff_stats_table.customContextMenuRequested.connect(self._buff_stats_context_menu)
        hdr_stats = self._buff_stats_table.horizontalHeader()
        hdr_stats.setSectionResizeMode(0, QHeaderView.Interactive)
        self._buff_stats_table.setColumnWidth(0, 240)
        hdr_stats.setSectionResizeMode(1, QHeaderView.Interactive)
        self._buff_stats_table.setColumnWidth(1, 100)
        hdr_stats.setStretchLastSection(False)
        self._buff_stats_table.verticalHeader().setDefaultSectionSize(24)
        self._buff_stats_table.setMinimumHeight(100)
        stf_layout.addWidget(self._buff_stats_table, 1)
        stats_table_frame.setMinimumHeight(120)
        stats_outer.addWidget(stats_table_frame)

        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.NoFrame)
        controls_scroll.setMinimumHeight(80)
        controls_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        controls_inner = QWidget()
        ctrl_layout = QVBoxLayout(controls_inner)
        ctrl_layout.setContentsMargins(0, 4, 0, 0)
        ctrl_layout.setSpacing(4)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        preset_row.addWidget(QLabel("Preset:"))
        self._buff_preset_combo = QComboBox()
        self._buff_preset_combo.addItems([
            "Max All (max every stat value, no hash changes)",
            "Max All Flat (max value on all flat stat entries)",
            "Max DDD (max value on flat2 entries)",
            "Max DPV (max value on flat2 entries)",
            "Max HP (max value on flat1 entries)",
            "Max All Rates (max value on all rate entries)",
            "Swap to DDD (change flat2 hashes to Damage)",
            "Swap to DPV (change flat2 hashes to Defense)",
            "Custom (pick stat + value)",
        ])
        self._buff_preset_combo.currentIndexChanged.connect(self._buff_preset_changed)
        preset_row.addWidget(self._buff_preset_combo, 1)

        add_buff_btn = QPushButton("Apply Preset")
        add_buff_btn.setObjectName("accentBtn")
        add_buff_btn.clicked.connect(self._buff_add_to_item)
        preset_row.addWidget(add_buff_btn)

        reset_btn = QPushButton("Reset")
        reset_btn.setToolTip("Discard all in-memory changes, re-extract from disk")
        reset_btn.clicked.connect(self._buff_remove_all)
        preset_row.addWidget(reset_btn)
        ctrl_layout.addLayout(preset_row)

        self._buff_custom_row = QWidget()
        custom_layout = QHBoxLayout(self._buff_custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(4)
        custom_layout.addWidget(QLabel("Stat:"))
        self._buff_type_combo = QComboBox()
        for name in BUFF_HASHES:
            self._buff_type_combo.addItem(name)
        custom_layout.addWidget(self._buff_type_combo)
        custom_layout.addWidget(QLabel("Value:"))
        self._buff_value_spin = QSpinBox()
        self._buff_value_spin.setRange(0, 999999999)
        self._buff_value_spin.setValue(1000000)
        self._buff_value_spin.setToolTip(
            "Flat stats (HP/DDD/DPV): use large values like 1,000,000\n"
            "Rate stats: 1 byte, 0-255 (max varies per stat type)\n"
            "  Combat rates (Speed/Crit/AtkSpd): typically 0-15\n"
            "  HealthRegen/StamRegen: up to 50\n"
            "  SkillExp/TrustGain: up to 10\n"
            "Invincible: 1 = on, 0 = off"
        )
        custom_layout.addWidget(self._buff_value_spin)
        self._buff_custom_row.setVisible(False)
        ctrl_layout.addWidget(self._buff_custom_row)

        edit_refine_row = QHBoxLayout()
        edit_refine_row.setSpacing(4)
        edit_refine_row.addWidget(QLabel("Edit Stat:"))
        self._buff_sel_value_spin = QSpinBox()
        self._buff_sel_value_spin.setRange(0, 999999999)
        self._buff_sel_value_spin.setValue(0)
        self._buff_sel_value_spin.setToolTip("Set the value for the selected base stat (Attack/Defense/DDD/DPV/HP etc.)")
        edit_refine_row.addWidget(self._buff_sel_value_spin)
        edit_sel_btn = QPushButton("Apply to Stat")
        edit_sel_btn.setToolTip("Change ONLY the clicked base stat")
        edit_sel_btn.clicked.connect(self._buff_apply_to_selected)
        edit_refine_row.addWidget(edit_sel_btn)

        self._buff_sel_label = QLabel("")
        self._buff_sel_label.setStyleSheet(f"color: {COLORS['accent']};")
        edit_refine_row.addWidget(self._buff_sel_label)

        edit_refine_row.addWidget(QLabel("Refine:"))
        self._buff_array_combo = QComboBox()
        self._buff_array_combo.addItem("All Levels (apply to every array)")
        self._buff_array_combo.setToolTip(
            "Select which refinement level to apply presets to.\n"
            "'All Levels' applies to every array (default).\n"
            "Select a specific level to edit only that refinement tier."
        )
        edit_refine_row.addWidget(self._buff_array_combo)
        ctrl_layout.addLayout(edit_refine_row)

        self._buff_edit_selected_row = QWidget()
        self._buff_array_row = QWidget()

        self._buff_stats_table.selectionModel().selectionChanged.connect(self._buff_on_stat_selected)

        passive_row = QHBoxLayout()
        passive_row.setSpacing(4)
        passive_row.addWidget(QLabel("Passive:"))

        self._eb_passive_combo = QComboBox()
        self._eb_passive_combo.setToolTip(
            "Change the passive skill on this item.\n"
            "This is the green text on tooltips like 'Lightning Resistance Lv 1'.\n"
            "Items without a passive can also have one added (new PAZ pack approach).")
        self._eb_passive_combo.setMinimumWidth(160)
        passive_row.addWidget(self._eb_passive_combo, 1)

        passive_row.addWidget(QLabel("Lv:"))
        self._eb_level_spin = QSpinBox()
        self._eb_level_spin.setRange(1, 100)
        self._eb_level_spin.setValue(1)
        self._eb_level_spin.setToolTip("Passive level (shown as 'Lv X' in-game)")
        self._eb_level_spin.setMinimumWidth(70)
        passive_row.addWidget(self._eb_level_spin)

        eb_apply_btn = QPushButton("Add Passive")
        eb_apply_btn.setObjectName("accentBtn")
        eb_apply_btn.setToolTip(
            "ADD a passive skill to this item (stacks with existing passives).\n"
            "You can add multiple passives to the same item.")
        eb_apply_btn.clicked.connect(self._eb_apply)
        passive_row.addWidget(eb_apply_btn)

        eb_remove_passive_btn = QPushButton("Remove")
        eb_remove_passive_btn.setToolTip("Remove selected passive from this item")
        eb_remove_passive_btn.clicked.connect(self._eb_remove_passive)
        passive_row.addWidget(eb_remove_passive_btn)

        god_mode_btn = QPushButton("God Mode")
        god_mode_btn.setToolTip(
            "Inject full God Mode stats into selected item:\n"
            "Invincible + Great Thief passives, max DDD/DPV,\n"
            "max regen, max speed/crit/resist, 8 equipment buffs.\n"
            "Based on Potter420's crimson-rs research.")
        god_mode_btn.setStyleSheet("background-color: #cc3333; color: white; font-weight: bold;")
        god_mode_btn.clicked.connect(self._eb_god_mode)
        passive_row.addWidget(god_mode_btn)

        self._eb_status = QLabel("")
        self._eb_status.setStyleSheet(f"color: {COLORS['accent']};")
        passive_row.addWidget(self._eb_status)

        ctrl_layout.addLayout(passive_row)

        effect_row = QHBoxLayout()
        effect_row.setSpacing(4)
        effect_row.addWidget(QLabel("Effect:"))

        self._effect_search = QLineEdit()
        self._effect_search.setPlaceholderText("Search effects (e.g. shadow, lightning, boot)...")
        self._effect_search.setToolTip("Filter effects by name, gimmick, skill ID, or source item.")
        self._effect_search.setMaximumWidth(220)
        self._effect_search.textChanged.connect(self._effect_filter_changed)
        effect_row.addWidget(self._effect_search)

        self._effect_catalog_combo = QComboBox()
        self._effect_catalog_combo.setToolTip(
            "Pick a gimmick effect from an existing in-game item.\n"
            "Copies gimmick_info, docking_child_data, cooltime,\n"
            "max_charged_useable_count, and passive skills.\n"
            "This makes the effect actually WORK, not just display.")
        self._effect_catalog_combo.setMinimumWidth(300)
        self._effect_catalog_combo.addItem("(load item data first)", None)
        effect_row.addWidget(self._effect_catalog_combo, 1)

        copy_effect_btn = QPushButton("Apply Effect")
        copy_effect_btn.setObjectName("accentBtn")
        copy_effect_btn.setToolTip(
            "Apply the selected gimmick effect to the current item.\n\n"
            "Passives STACK — existing passives stay, new ones added.\n"
            "Apply multiple effects to combine their skills.\n\n"
            "Gimmick/docking REPLACES — only one gimmick slot per item.\n"
            "The last-applied gimmick is what activates the effects.")
        copy_effect_btn.clicked.connect(self._eb_copy_effect)
        effect_row.addWidget(copy_effect_btn)

        ctrl_layout.addLayout(effect_row)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        preset_row.addWidget(QLabel("Presets:"))

        shadow_boots_btn = QPushButton("Shadow Boots")
        shadow_boots_btn.setToolTip(
            "Apply Potter's Shadow Boots config to selected item:\n"
            "Skills: Shadow Dash (7201) + Breeze Step (7055) + Swimming (7202)\n"
            "Gimmick: 1004431 (boots gimmick — actually activates the skills)\n"
            "Socket: Bip01 Footsteps\n\n"
            "Works on any boots. Passives stack with existing.")
        shadow_boots_btn.setStyleSheet("background-color: #4A148C; color: white; font-weight: bold;")
        shadow_boots_btn.clicked.connect(lambda: self._eb_apply_preset("shadow_boots"))
        preset_row.addWidget(shadow_boots_btn)

        lightning_btn = QPushButton("Lightning Weapon")
        lightning_btn.setToolTip(
            "Apply lightning weapon config (Potter's Hwando recipe):\n"
            "Skills: Lightning (91101) + Fire (91105) + Ice (91104) affinity\n"
            "Gimmick: 1001961 (weapon gimmick)\n"
            "Socket: Gimmick_Weapon_00_Socket\n\n"
            "NOTE: Lightning is pure VFX on one-handed weapons.\n"
            "Works fully on twohand sword, hammer, spear, glove.")
        lightning_btn.setStyleSheet("background-color: #FFB300; color: black; font-weight: bold;")
        lightning_btn.clicked.connect(lambda: self._eb_apply_preset("lightning_weapon"))
        preset_row.addWidget(lightning_btn)

        great_thief_btn = QPushButton("Great Thief")
        great_thief_btn.setToolTip(
            "Apply Great Thief activated skill (works on ANY item).\n"
            "Opens a picker to choose:\n"
            "  - Block Theft only (9128 + 76009)\n"
            "  - Block ALL crime (+76011 + 76012) — full crime immunity\n\n"
            "Gimmick: 1002041, 1 charge, 30-min cooldown\n"
            "Tip: Use 'No Cooldown (All Items)' for unlimited pickpocketing.")
        great_thief_btn.setStyleSheet("background-color: #00695C; color: white; font-weight: bold;")
        great_thief_btn.clicked.connect(self._eb_great_thief_pick_variant)
        preset_row.addWidget(great_thief_btn)

        preset_row.addStretch()
        ctrl_layout.addLayout(preset_row)

        gimmick_row = QHBoxLayout()
        gimmick_row.setSpacing(4)
        gimmick_row.addWidget(QLabel("Gimmick:"))
        self._eb_vfx_combo = QComboBox()
        self._eb_vfx_combo.setEditable(True)
        self._eb_vfx_combo.setInsertPolicy(QComboBox.NoInsert)
        self._eb_vfx_combo.lineEdit().setPlaceholderText(
            "Search gimmicks (lantern, lightning, flame, drone, thief...)")
        self._eb_vfx_combo.setMinimumWidth(300)
        self._eb_vfx_combo.setToolTip(
            "Attach any equip-gimmick to the current item. Clones gimmick_info,\n"
            "docking_child_data, cooltime, and charge config from a sample\n"
            "in-game item that uses this gimmick.\n\n"
            "Examples:\n"
            "  gimmick_equip_lantern_01 — adds lantern charge behavior\n"
            "  gimmick_equip_lightning_OneHandSword — lightning VFX on weapon\n"
            "  gimmick_equip_Drone_Backpack — spawns a drone on equip\n\n"
            "Replaces any existing gimmick on the target item.")
        from PySide6.QtWidgets import QCompleter as _QCv
        self._eb_vfx_combo.completer().setCompletionMode(_QCv.PopupCompletion)
        self._eb_vfx_combo.completer().setFilterMode(Qt.MatchContains)
        self._load_vfx_catalog_into_combo()
        gimmick_row.addWidget(self._eb_vfx_combo, 1)

        apply_gimmick_btn = QPushButton("Apply Gimmick")
        apply_gimmick_btn.setStyleSheet("background-color: #006064; color: white; font-weight: bold;")
        apply_gimmick_btn.setToolTip(
            "Apply the selected gimmick to the current item.\n"
            "Replaces any existing gimmick slot — one gimmick per item.")
        apply_gimmick_btn.clicked.connect(self._eb_apply_vfx_gimmick)
        gimmick_row.addWidget(apply_gimmick_btn)

        gimmick_row.addStretch()
        ctrl_layout.addLayout(gimmick_row)

        self._eb_socket_row_widget = QWidget()
        socket_row = QHBoxLayout(self._eb_socket_row_widget)
        socket_row.setContentsMargins(0, 0, 0, 0)
        socket_row.setSpacing(4)
        socket_row.addWidget(QLabel("Sockets:"))
        self._eb_socket_count = QSpinBox()
        self._eb_socket_count.setRange(1, 8)
        self._eb_socket_count.setValue(5)
        self._eb_socket_count.setToolTip(
            "Target max socket count. Writes to drop_default_data.\n"
            "add_socket_material_item_list. The game validates this\n"
            "length matches the expected socket count.")
        socket_row.addWidget(self._eb_socket_count)
        socket_row.addWidget(QLabel("Pre-unlocked:"))
        self._eb_socket_valid = QSpinBox()
        self._eb_socket_valid.setRange(0, 8)
        self._eb_socket_valid.setValue(0)
        self._eb_socket_valid.setToolTip(
            "How many sockets are unlocked on drop.\n"
            "Extra sockets need to be unlocked at the Witch NPC.")
        socket_row.addWidget(self._eb_socket_valid)
        socket_apply_btn = QPushButton("Extend Sockets")
        socket_apply_btn.setToolTip(
            "Extend socket capacity on this item.\n"
            "Only applies to items with use_socket=1 (abyss gear).")
        socket_apply_btn.clicked.connect(self._eb_extend_sockets)
        socket_row.addWidget(socket_apply_btn)
        socket_row.addStretch()
        self._eb_socket_row_widget.setVisible(self._experimental_mode)
        ctrl_layout.addWidget(self._eb_socket_row_widget)

        self._PASSIVE_SKILL_NAMES = {}
        try:
            import json as _json
            for base in [os.path.dirname(os.path.abspath(__file__)),
                         getattr(sys, '_MEIPASS', ''), os.getcwd()]:
                _sep = os.path.join(base, 'skill_english_names.json')
                if os.path.isfile(_sep):
                    with open(_sep, 'r', encoding='utf-8') as _sf:
                        _all_skills = _json.load(_sf)
                    for _sk, _sv in _all_skills.items():
                        _key = int(_sk)
                        _name = _sv.get('english_name', '')
                        _internal = _sv.get('skill_name', '')
                        if (_internal.startswith('Equip_Passive_') or
                            _internal.startswith('Equip_Socket_Passive_') or
                            _key in (70994, 9128)):
                            if _name:
                                self._PASSIVE_SKILL_NAMES[_key] = _name
                            else:
                                clean = _internal.replace('Equip_Socket_Passive_', '').replace('Equip_Passive_', '').replace('_', ' ')
                                self._PASSIVE_SKILL_NAMES[_key] = clean
                    log.info("Loaded %d passive skills from skill_english_names.json",
                             len(self._PASSIVE_SKILL_NAMES))
                    break
        except Exception as _e:
            log.warning("Passive skill load failed: %s", _e)

        _fallbacks = {
            70994: "Invincible", 9128: "Great Thief",
            8037: "Fire Resistance", 8038: "Ice Resistance", 8039: "Lightning Resistance",
            7201: "Flying Boots", 7202: "Swimming Boots", 7204: "Equip Drop Rate",
        }
        for _fk, _fv in _fallbacks.items():
            if _fk not in self._PASSIVE_SKILL_NAMES:
                self._PASSIVE_SKILL_NAMES[_fk] = _fv

        self._eb_passive_combo.setEditable(True)
        self._eb_passive_combo.setInsertPolicy(QComboBox.NoInsert)
        self._eb_passive_combo.lineEdit().setPlaceholderText("Type to search passives...")
        from PySide6.QtWidgets import QCompleter
        self._eb_passive_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self._eb_passive_combo.completer().setFilterMode(Qt.MatchContains)

        self._buff_skill_descs = {}
        _desc_path = os.path.join(os.path.dirname(__file__), "buff_skill_descriptions.json")
        if os.path.isfile(_desc_path):
            try:
                with open(_desc_path, "r", encoding="utf-8") as _df:
                    self._buff_skill_descs = json.load(_df)
            except Exception:
                pass

        for sk in sorted(self._PASSIVE_SKILL_NAMES.keys()):
            name = self._PASSIVE_SKILL_NAMES[sk]
            desc = self._buff_skill_descs.get(str(sk), {}).get("description", "")
            label = f"{name} ({sk})" + (f" — {desc}" if desc else "")
            self._eb_passive_combo.addItem(label, sk)

        self._EQUIP_BUFF_NAMES = {
            1000001: "Max HP (HP)",
            1000002: "Max Spirit (MP)",
            1000003: "Max Stamina (SP)",
            1000004: "Damage Dealt (DDD)",
            1000005: "Defense (DPV)",
            1000006: "Attack Speed (AttackSpeedRate)",
            1000008: "HP Regen",
            1000009: "Spirit Regen (MP Regen)",
            1000046: "Stamina Regen",
            1000012: "Fire Resistance (Burn/Heat Immunity)",
            1000013: "Ice Resistance (Freeze/Cold Immunity)",
            1000014: "Lightning Resistance (Shock Immunity)",
            1000090: "Dmg vs Machines",
            1000108: "Dmg vs Earthen Beings (Hexe)",
            1000109: "Dmg vs Humanoids",
            1000110: "Dmg vs Walkers (Golems)",
            1000111: "Dmg vs Mighty Foes (Bosses)",
            1000112: "Dmg vs Beasts (Animals)",
            1000113: "Dmg vs Abyssal Creatures",
            1000147: "Crit vs Plate Armor",
            1000154: "Crit vs Leather Armor",
            1000157: "Crit vs Cloth Armor",
            1000096: "Damage Reduction (Aegis)",
            1000097: "Guard Stamina Cost Reduction",
            1000066: "Disarm on Hit (Equip Drop)",
            1000116: "Arrow Save Chance (Block Ammo)",
            1000141: "Stamina Regen Rate Change",
            1000015: "Stamina Cost Reduction",
            1000091: "Craft Material Save",
            1000071: "Bonus Ore Drop",
            1000072: "Bonus Plant Drop",
            1000073: "Bonus Animal Drop",
            1000093: "Bonus Log Drop",
            1000105: "Bonus Log Drop (Tool)",
            1000176: "Bonus Mining Drop (Tool)",
            1000117: "Silver Drop Rate",
            1000100: "Climb Speed",
            1000107: "Swim Speed",
            1000089: "Food Effect Lv Up",
            1000099: "NPC Trust Gain (Affinity)",
            1000114: "Contribution EXP Gain",
            1000115: "Skill EXP Gain",
            1000119: "Great Thief (Bonus Theft)",
            1000123: "Bonus Crafting Result Chance",
            1000124: "Pet Trust Gain",
            1000132: "Solidarity - Trust Boost (SizlekSword)",
            1000133: "Equestrian - Horse EXP Boost (MasterDoo)",
            1000081: "Daze Immunity (Boss)",
            1000149: "Abyss Toxin Immunity",
            1000150: "Poison Immunity",
            1000151: "Bismuth Immunity",
            1000191: "Sleep Immunity",
            1000192: "Daze Immunity (Food)",
            1000130: "Myurdin Sword Passive",
            1000131: "Split-Horn Sword Passive",
            1000134: "Companionship - Pet Trust Boost (Crowman Sword)",
            1000136: "Reed Devil Sword Passive",
            1000148: "Soul Knight Sword Passive",
            1000152: "Deer King Helm Passive",
            1000153: "Kill Resource Recovery",
            1000161: "White Horn Passive",
            1000030: "HP DOT (Damage Over Time)",
            1000051: "HP DOT Damage",
            1000052: "Spirit DOT Damage",
            1000053: "Stamina DOT Damage",
            1000200: "Wolf Disguise",
            1000201: "Bear Disguise",
            1000202: "Deer Disguise",
            1000203: "Wildlife Disguise",
            1000087: "Civilian Disguise",
        }

        level_row = QHBoxLayout()
        level_row.setSpacing(4)
        level_row.addWidget(QLabel("Enchant level:"))
        self._eb_level_target = QComboBox()
        self._eb_level_target.addItem("All Levels (0-10)", -1)
        for i in range(11):
            self._eb_level_target.addItem(f"Level +{i} only", i)
        self._eb_level_target.setToolTip(
            "Which enchant level(s) to apply stat/buff changes to.\n"
            "'All Levels' applies the same value to every level.\n"
            "Pick a specific level to only modify that one.\n\n"
            "Items have 11 enchant levels (0-10). Each level can have\n"
            "different stat values. The game uses the level matching\n"
            "your item's current enchantment.")
        self._eb_level_target.setFixedWidth(160)
        self._eb_level_target.currentIndexChanged.connect(
            lambda: self._buff_refresh_stats() if self._buff_current_item else None)
        level_row.addWidget(self._eb_level_target)
        hint = QLabel("  Pick level \u2192 Add stats/buffs \u2192 Export as Mod")
        hint.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        level_row.addWidget(hint)
        level_row.addStretch()
        ctrl_layout.addLayout(level_row)

        stat_row = QHBoxLayout()
        stat_row.setSpacing(4)
        stat_row.addWidget(QLabel("Stat:"))

        self._eb_stat_combo = QComboBox()
        self._eb_stat_combo.setEditable(True)
        self._eb_stat_combo.setInsertPolicy(QComboBox.NoInsert)
        self._eb_stat_combo.lineEdit().setPlaceholderText("Type to search stats...")
        from PySide6.QtWidgets import QCompleter as _QC2
        self._eb_stat_combo.completer().setCompletionMode(_QC2.PopupCompletion)
        self._eb_stat_combo.completer().setFilterMode(Qt.MatchContains)

        self._ENCHANT_STAT_LIST = [
            ("DDD / Damage", 1000002, "stat_list_static", 999999),
            ("DPV / Defense", 1000003, "stat_list_static", 999999),
            ("Max HP", 1000000, "stat_list_static", 999999),
            ("Critical Damage", 1000006, "stat_list_static", 999999),
            ("Incoming Damage Rate", 1000008, "stat_list_static", 999999),
            ("Incoming Damage Reduction", 1000009, "stat_list_static", 999999),
            ("DHIT / Accuracy", 1000004, "stat_list_static", 999999),
            ("DDV / Base Attack", 1000005, "stat_list_static", 999999),
            ("Stamina Cost Reduction", 1000037, "stat_list_static", 100000000),
            ("MP Cost Reduction", 1000046, "stat_list_static", 100000000),
            ("Max Damage Rate", 1000035, "stat_list_static", 999999),
            ("DPV Rate (%)", 1000050, "stat_list_static", 999999),
            ("Pressure", 1000036, "stat_list_static", 999999),
            ("Attack Speed", 1000010, "stat_list_static_level", 15),
            ("Move Speed", 1000011, "stat_list_static_level", 15),
            ("Crit Rate", 1000007, "stat_list_static_level", 15),
            ("Climb Speed", 1000012, "stat_list_static_level", 15),
            ("Swim Speed", 1000013, "stat_list_static_level", 15),
            ("Fire Resistance", 1000016, "stat_list_static_level", 15),
            ("Ice Resistance", 1000017, "stat_list_static_level", 15),
            ("Lightning Resistance", 1000018, "stat_list_static_level", 15),
            ("Guard PV Rate", 1000043, "stat_list_static_level", 15),
            ("Hit Rate", 1000031, "stat_list_static_level", 15),
            ("Equip Drop Rate", 1000049, "stat_list_static_level", 15),
            ("Add Money Drop Rate", 1000047, "stat_list_static_level", 15),
            ("HP Regen", 1000000, "regen_stat_list", 1000000),
            ("Stamina Regen", 1000026, "regen_stat_list", 100000),
            ("MP Regen", 1000027, "regen_stat_list", 100000),
        ]
        for idx, (sname, skey, slist, sdefault) in enumerate(self._ENCHANT_STAT_LIST):
            label_type = slist.replace('stat_list_', '').replace('_', ' ')
            self._eb_stat_combo.addItem(f"{sname} [{label_type}] ({skey})", idx)
        stat_row.addWidget(self._eb_stat_combo, 1)

        stat_row.addWidget(QLabel("Val:"))
        self._eb_stat_value = QSpinBox()
        self._eb_stat_value.setRange(0, 999999999)
        self._eb_stat_value.setValue(999999)
        self._eb_stat_value.setToolTip(
            "Flat stats (DDD/DPV): 999,999 = strong, 1,000,000 = dev ring\n"
            "Rate stats (Speed/Crit): 0-15 where 15 = max\n"
            "Regen: 100,000 = very fast, 1,000,000 = dev ring")
        self._eb_stat_value.setMinimumWidth(100)
        stat_row.addWidget(self._eb_stat_value)

        stat_add_btn = QPushButton("Add Stat")
        stat_add_btn.setObjectName("accentBtn")
        stat_add_btn.setToolTip("Add this stat to ALL enchant levels (structural edit)")
        stat_add_btn.clicked.connect(self._eb_add_stat)
        stat_row.addWidget(stat_add_btn)

        stat_remove_btn = QPushButton("Remove")
        stat_remove_btn.setToolTip("Remove this stat from ALL enchant levels")
        stat_remove_btn.clicked.connect(self._eb_remove_stat)
        stat_row.addWidget(stat_remove_btn)

        ctrl_layout.addLayout(stat_row)

        eb_row = QHBoxLayout()
        eb_row.setSpacing(4)
        eb_row.addWidget(QLabel("Equip Buff:"))

        self._eb_buff_combo = QComboBox()
        self._eb_buff_combo.setToolTip(
            "Select an equipment buff to add to ALL enchant levels.\n"
            "These are the colored effects on items (Fire Res, Ice Res, etc).\n"
            "Requires Export as Mod (structural change).")
        self._eb_buff_combo.setMinimumWidth(180)
        try:
            import json as _json
            _search_dirs = [os.path.dirname(os.path.abspath(__file__)),
                            getattr(sys, '_MEIPASS', ''), os.getcwd()]

            for base in _search_dirs:
                _bdp = os.path.join(base, 'buff_database.json')
                if os.path.isfile(_bdp):
                    with open(_bdp, 'r', encoding='utf-8') as _bf:
                        _all_buffs = _json.load(_bf)
                    for _bk, _bv in _all_buffs.items():
                        _key = int(_bk)
                        if _key not in self._EQUIP_BUFF_NAMES:
                            _n = _bv.get('name', _bv.get('internal', ''))
                            if not _n:
                                _n = _bv.get('clean', '')
                            _n = _n.replace('BuffLevel_', '').replace('_', ' ')
                            if _n:
                                self._EQUIP_BUFF_NAMES[_key] = _n
                    log.info("Loaded %d buffs from buff_database.json (total: %d)",
                             len(_all_buffs), len(self._EQUIP_BUFF_NAMES))
                    break

            for base in _search_dirs:
                _bep = os.path.join(base, 'buff_english_names.json')
                if os.path.isfile(_bep):
                    with open(_bep, 'r', encoding='utf-8') as _bf:
                        _eng_buffs = _json.load(_bf)
                    _eng_count = 0
                    for _bk, _bv in _eng_buffs.items():
                        _key = int(_bk)
                        _eng = _bv.get('english_name', '')
                        if _eng:
                            self._EQUIP_BUFF_NAMES[_key] = _eng
                            _eng_count += 1
                    log.info("Applied %d English buff names", _eng_count)
                    break

            for base in _search_dirs:
                _cnp = os.path.join(base, 'buff_names_community.json')
                if os.path.isfile(_cnp):
                    with open(_cnp, 'r', encoding='utf-8') as _cf:
                        _cdata = _json.load(_cf)
                    _c_count = 0
                    for _entry in _cdata.get('buffs', []):
                        _key = _entry.get('key', 0)
                        _name = _entry.get('name', '')
                        _effect = _entry.get('effect', '')
                        if _key > 0 and _name:
                            _display = _name
                            if _effect and _effect != _name:
                                _display = f"{_name} — {_effect[:40]}"
                            self._EQUIP_BUFF_NAMES[_key] = _display
                            _c_count += 1
                        _mn = _entry.get('minValue')
                        _mx = _entry.get('maxValue')
                        _vt = _entry.get('valueType', '')
                        if _mn is not None and _mx is not None:
                            self._buff_community_ranges[_key] = (_mn, _mx, _vt)
                    log.info("Applied %d community buff names, %d with ranges from %s",
                             _c_count, len(self._buff_community_ranges), _cnp)
                    break
        except Exception as _e:
            log.warning("Buff name load failed: %s", _e)

        self._eb_buff_combo.setEditable(True)
        self._eb_buff_combo.setInsertPolicy(QComboBox.NoInsert)
        self._eb_buff_combo.lineEdit().setPlaceholderText("Type to search buffs...")
        from PySide6.QtCore import QSortFilterProxyModel
        from PySide6.QtWidgets import QCompleter
        self._eb_buff_combo.completer().setCompletionMode(QCompleter.PopupCompletion)
        self._eb_buff_combo.completer().setFilterMode(Qt.MatchContains)

        for bk in sorted(self._EQUIP_BUFF_NAMES.keys()):
            bname = self._EQUIP_BUFF_NAMES[bk]
            desc = self._buff_skill_descs.get(str(bk), {}).get("description", "")
            label = f"{bname} ({bk})" + (f" — {desc}" if desc else "")
            self._eb_buff_combo.addItem(label, bk)
        eb_row.addWidget(self._eb_buff_combo, 1)

        eb_row.addWidget(QLabel("Lv:"))
        self._eb_buff_level = QSpinBox()
        self._eb_buff_level.setRange(0, 100)
        self._eb_buff_level.setValue(15)
        self._eb_buff_level.setToolTip("Buff level (0-100, 15 = max for most buffs)")
        self._eb_buff_level.setMinimumWidth(60)
        eb_row.addWidget(self._eb_buff_level)

        self._buff_range_label = QLabel("")
        self._buff_range_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        eb_row.addWidget(self._buff_range_label)

        self._buff_community_ranges = {}
        self._eb_buff_combo.currentIndexChanged.connect(self._buff_on_buff_selected)

        eb_add_btn = QPushButton("Add Buff")
        eb_add_btn.setObjectName("accentBtn")
        eb_add_btn.setToolTip("Add this equipment buff to ALL enchant levels of the selected item")
        eb_add_btn.clicked.connect(self._eb_add_buff)
        eb_row.addWidget(eb_add_btn)

        eb_remove_btn = QPushButton("Remove Buff")
        eb_remove_btn.setToolTip("Remove this buff from ALL enchant levels of the selected item")
        eb_remove_btn.clicked.connect(self._eb_remove_buff)
        eb_row.addWidget(eb_remove_btn)

        ctrl_layout.addLayout(eb_row)


        self._cd_patches: dict = {}

        util_row = QHBoxLayout()
        util_row.setSpacing(4)

        no_cd_btn = QPushButton("No Cooldown (All Items)")
        no_cd_btn.setToolTip(
            "Scan every item in iteminfo.pabgb and queue cooldown → 1s for all items that have one.\n"
            "Included in the next Export JSON Patch — same as Pldada's No Cooldown mod.")
        no_cd_btn.clicked.connect(self._cd_patch_all_items)
        util_row.addWidget(no_cd_btn)

        util_row.addWidget(QLabel("Charges:"))
        self._max_charges_spin = QSpinBox()
        self._max_charges_spin.setRange(1, 99)
        self._max_charges_spin.setValue(30)
        self._max_charges_spin.setToolTip(
            "Target max charges per activated item.\n"
            "Vanilla highest is 30. Values above this may be clamped by\n"
            "the game and show as '0 uses' in-game.")
        util_row.addWidget(self._max_charges_spin)

        max_charges_btn = QPushButton("Max Charges (All Items)")
        max_charges_btn.setToolTip(
            "Set max_charged_useable_count to the chosen value on every item\n"
            "that uses charges (item_charge_type != 0 items are skipped).\n"
            "Only takes effect on FRESH copies obtained after applying.")
        max_charges_btn.clicked.connect(self._max_charges_all_items)
        util_row.addWidget(max_charges_btn)

        self._stack_check = QCheckBox("Max Stacks:")
        self._stack_check.setStyleSheet(
            f"color: {COLORS['accent']}; font-weight: bold;"
        )
        self._stack_check.setToolTip(
            "When checked, clicking 'Export JSON Patch' also includes max stack\n"
            "changes. Replaces FatStacks mod."
        )
        util_row.addWidget(self._stack_check)

        self._stack_spin = QSpinBox()
        self._stack_spin.setRange(1, 2147483647)
        self._stack_spin.setValue(9999)
        self._stack_spin.setToolTip("Custom max stack size for all stackable items")
        self._stack_spin.setFixedWidth(80)
        util_row.addWidget(self._stack_spin)

        self._inf_dura_check = QCheckBox("Infinity Durability")
        self._inf_dura_check.setStyleSheet(
            f"color: {COLORS['accent']}; font-weight: bold;"
        )
        self._inf_dura_check.setToolTip(
            "When checked, every export (JSON Patch / Mod / CDUMM) sets\n"
            "max_endurance = 65535 and is_destroy_when_broken = 0 on all\n"
            "items that have durability. Works alongside Max Stacks,\n"
            "cooldowns, transmog, ItemBuffs configs, etc.\n\n"
            "Replaces the Pldada Infinity Durability byte-patch mod."
        )
        util_row.addWidget(self._inf_dura_check)

        util_row.addStretch(1)
        ctrl_layout.addLayout(util_row)

        self._buff_desc_label = QLabel()
        self._buff_desc_label.setWordWrap(True)
        self._buff_desc_label.setStyleSheet(
            f"color: {COLORS['text_dim']}; padding: 2px; font-size: 10px;"
        )
        self._buff_desc_label.setVisible(False)
        self._buff_preset_combo.currentIndexChanged.connect(self._buff_update_desc)
        self._buff_type_combo.currentTextChanged.connect(self._buff_update_desc)
        self._buff_update_desc()
        ctrl_layout.addWidget(self._buff_desc_label)

        controls_scroll.setWidget(controls_inner)
        stats_outer.addWidget(controls_scroll)

        stats_outer.setStretchFactor(0, 4)
        stats_outer.setStretchFactor(1, 1)
        stats_outer.setSizes([400, 120])

        stats_outer.setMinimumWidth(200)
        buff_splitter.addWidget(stats_outer)

        buff_splitter.setStretchFactor(0, 1)
        buff_splitter.setStretchFactor(1, 6)
        buff_splitter.setSizes([150, 700])
        layout.addWidget(buff_splitter, 1)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(6)

        export_buffs_btn = QPushButton("Export JSON Patch")
        export_buffs_btn.setToolTip(
            "Export value-only changes as a JSON patch file.\n"
            "Only works for value edits and hash swaps (no size changes).\n"
            "For structural edits (add buffs/stats), use 'Export as Mod'."
        )
        export_buffs_btn.clicked.connect(self._buff_export_json)
        bottom_bar.addWidget(export_buffs_btn)

        export_mod_btn = QPushButton("Export as Mod")
        export_mod_btn.setObjectName("accentBtn")
        export_mod_btn.setStyleSheet("background-color: #7B1FA2; color: white; font-weight: bold;")
        export_mod_btn.setToolTip(
            "Export ALL changes as raw game files in a mod folder.\n"
            "Output: files/gamedata/.../iteminfo.pabgb + modinfo.json\n"
            "For CD JSON Mod Manager or DMM."
        )
        export_mod_btn.clicked.connect(self._buff_export_mod)
        bottom_bar.addWidget(export_mod_btn)

        export_cdumm_btn = QPushButton("Export as CDUMM Mod")
        export_cdumm_btn.setObjectName("accentBtn")
        export_cdumm_btn.setStyleSheet("background-color: #1B5E20; color: white; font-weight: bold;")
        export_cdumm_btn.setToolTip(
            "Export ALL changes as a CDUMM-compatible mod folder.\n"
            "Uses pack_mod to generate proper PAZ archives.\n\n"
            "Output: 0036/ (0.paz + 0.pamt) + meta/ (0.papgt) + modinfo.json\n"
            "Import directly into CDUMM mod manager."
        )
        export_cdumm_btn.clicked.connect(self._buff_export_cdumm_mod)
        bottom_bar.addWidget(export_cdumm_btn)

        apply_game_btn = QPushButton("Apply to Game")
        apply_game_btn.setStyleSheet("background-color: #B71C1C; color: white; font-weight: bold;")
        apply_game_btn.setToolTip(
            "Deploy modified iteminfo.pabgb directly to the game.\n"
            "Creates a PAZ overlay — original files are NOT modified.\n"
            "Restart the game for changes to take effect.\n"
            "Use Restore to undo.\n\n"
            "(Experimental — enable in Dev menu)"
        )
        apply_game_btn.clicked.connect(self._buff_apply_to_game)
        apply_game_btn.setVisible(self._experimental_mode)
        self._buff_apply_game_btn = apply_game_btn
        bottom_bar.addWidget(apply_game_btn)

        import_community_btn = QPushButton("Import Community JSON")
        import_community_btn.setToolTip(
            "Import a Pldada/DMM-format JSON byte patch (e.g. Infinity Durability).\n"
            "Applies to the extracted vanilla data BEFORE your edits.\n"
            "Combined with your ItemBuffs changes when you Export as Mod."
        )
        import_community_btn.clicked.connect(self._buff_import_community_json)
        bottom_bar.addWidget(import_community_btn)

        sync_names_btn = QPushButton("Sync Buff Names")
        sync_names_btn.setToolTip(
            "Download latest community-verified buff/stat/passive names from GitHub.\n"
            "Updates display names in this tab. Contribute corrections via PR:\n"
            "github.com/NattKh/CrimsonDesertCommunityItemMapping/buff_names_community.json"
        )
        sync_names_btn.clicked.connect(self._buff_sync_community_names)
        bottom_bar.addWidget(sync_names_btn)

        save_cfg_btn = QPushButton("Save Config")
        save_cfg_btn.setToolTip(
            "Save your current edits as a reusable config file.\n"
            "Share with others or load back later to tweak and re-export."
        )
        save_cfg_btn.clicked.connect(self._buff_save_config)
        bottom_bar.addWidget(save_cfg_btn)

        load_cfg_btn = QPushButton("Load Config")
        load_cfg_btn.setToolTip(
            "Load a previously saved config file.\n"
            "Re-applies edits to fresh game data so you can tweak and re-export."
        )
        load_cfg_btn.clicked.connect(self._buff_load_config)
        bottom_bar.addWidget(load_cfg_btn)

        restore_btn = QPushButton("Restore Original")
        restore_btn.setStyleSheet("background-color: #37474F; color: white; font-weight: bold;")
        restore_btn.setToolTip(
            "Undo 'Apply to Game': remove the ItemBuffs PAZ overlay and its\n"
            "PAPGT entry (preserves other overlays like Stores). Requires admin.\n"
            "Restart the game after restoring."
        )
        restore_btn.clicked.connect(self._buff_restore_original)
        bottom_bar.addWidget(restore_btn)

        reset_vanilla_btn = QPushButton("Reset to Vanilla PAPGT")
        reset_vanilla_btn.setStyleSheet("background-color: #4A148C; color: white; font-weight: bold;")
        reset_vanilla_btn.setToolTip(
            "NUCLEAR RECOVERY: copy .papgt.vanilla (first-apply snapshot) over\n"
            "meta/0.papgt. Disables ALL overlays (stores, fields, buffs, etc).\n"
            "Use when the game won't launch after a bad apply/restore.\n"
            "You'll need to re-apply any other mods afterward. Requires admin."
        )
        reset_vanilla_btn.clicked.connect(self._buff_reset_vanilla_papgt)
        bottom_bar.addWidget(reset_vanilla_btn)

        bottom_bar.addWidget(QLabel("JSON Load Order:"))
        self._buff_overlay_spin = QSpinBox()
        self._buff_overlay_spin.setRange(1, 9999)
        self._buff_overlay_spin.setValue(self._config.get("buff_overlay_dir", 58))
        self._buff_overlay_spin.setFixedWidth(70)
        self._buff_overlay_spin.setToolTip(
            "PAZ folder slot used by 'Export JSON Patch'.\n"
            "Default: 0058. Change if another mod already uses slot 0058."
        )
        self._buff_overlay_spin.valueChanged.connect(
            lambda v: self._config.update({"buff_overlay_dir": v}) or self.config_save_requested.emit()
        )
        bottom_bar.addWidget(self._buff_overlay_spin)

        bottom_bar.addWidget(QLabel("Mod Load Order:"))
        self._buff_modgroup_spin = QSpinBox()
        self._buff_modgroup_spin.setRange(1, 9999)
        self._buff_modgroup_spin.setValue(self._config.get("buff_mod_group", 36))
        self._buff_modgroup_spin.setFixedWidth(70)
        self._buff_modgroup_spin.setToolTip(
            "PAZ folder slot used by 'Export as Mod' (CDUMM-compatible).\n"
            "Default: 0036. Change if another mod already uses slot 0036."
        )
        self._buff_modgroup_spin.valueChanged.connect(
            lambda v: self._config.update({"buff_mod_group": v}) or self.config_save_requested.emit()
        )
        bottom_bar.addWidget(self._buff_modgroup_spin)

        preview_btn = QPushButton("Preview Item")
        preview_btn.setToolTip(
            "Show a preview of how the selected item will look in-game\n"
            "with all your current modifications applied."
        )
        preview_btn.setStyleSheet("background-color: #1565C0; color: white; font-weight: bold;")
        preview_btn.clicked.connect(self._buff_preview_item)
        bottom_bar.addWidget(preview_btn)

        credit = QLabel("credit: Potter420 & LukeFZ")
        credit.setStyleSheet("color: #FF5252; font-style: italic; padding: 2px;")
        bottom_bar.addWidget(credit)

        self._buff_status_label = QLabel("")
        self._buff_status_label.setWordWrap(True)
        self._buff_status_label.setStyleSheet(
            f"color: {COLORS['text_dim']}; padding: 2px;"
        )
        layout.addWidget(self._buff_status_label)

        _bb_widget = QWidget()
        _bb_widget.setLayout(bottom_bar)
        _bb_scroll = QScrollArea()
        _bb_scroll.setWidget(_bb_widget)
        _bb_scroll.setWidgetResizable(True)
        _bb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        _bb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        _bb_scroll.setFrameShape(QFrame.NoFrame)
        _bb_scroll.setFixedHeight(44)
        layout.addWidget(_bb_scroll)

        self._item_buffs_tab_widget = self
        self._itembuffs_tab_widget = self

        self._buff_patcher: Optional[ItemBuffPatcher] = None
        self._buff_rust_items: Optional[list] = None
        self._buff_rust_lookup: dict = {}
        self._buff_use_rust: bool = False
        self._buff_data: Optional[bytearray] = None
        self._buff_items: List[ItemRecord] = []
        self._buff_current_item: Optional[ItemRecord] = None
        self._armor_catalog: list = []
        self._transmog_swaps: list = []
        self._vfx_summaries: list = []
        self._vfx_size_changes: list = []
        self._vfx_swaps: list = []
        self._vfx_anim_swaps: list = []
        self._vfx_attach_changes: list = []

        self._buff_item_limits = {}
        try:
            import json as _json
            limits_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'item_limits.json')
            if os.path.isfile(limits_path):
                with open(limits_path, 'r') as _f:
                    self._buff_item_limits = _json.load(_f).get('items', {})
        except Exception:
            pass
        self._buff_modified = False


    def _effect_swap_blackberry_test(self) -> None:
        """Test: swap Blackberry effect to instant Dragon CD reset."""
        game_path = self._paz_game_path.text().strip()
        if not game_path:
            QMessageBox.warning(self, "No Game Path", "Set the game install path first.")
            return
        if not _is_admin():
            QMessageBox.warning(self, "Admin Required",
                                "Writing to game files requires administrator privileges.\n"
                                "Right-click → Run as administrator")
            return

        reply = QMessageBox.question(
            self, "Item Effect Swap",
            "Swap Blackberry's food effect with Narima's Horn instant Dragon CD reset?\n\n"
            "This patches iteminfo.pabgb. A backup will be created.\n"
            "Use Steam Verify Integrity to undo.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._effect_status.setText("Patching...")
        QApplication.processEvents()

        try:
            patcher = ItemEffectPatcher(game_path)
            ok, msg = patcher.swap_effect('Blackberry', 0xB0A8256B)
            self._effect_status.setText("OK" if ok else "FAILED")
            if ok:
                QMessageBox.information(self, "Effect Swapped", msg)
            else:
                QMessageBox.critical(self, "Failed", msg)
        except Exception as e:
            self._effect_status.setText(f"Error: {e}")
            QMessageBox.critical(self, "Error", str(e))


    def _effect_check_blackberry(self) -> None:
        """Check current effect hash on Blackberry."""
        game_path = self._paz_game_path.text().strip()
        if not game_path:
            QMessageBox.warning(self, "No Game Path", "Set the game install path first.")
            return
        try:
            patcher = ItemEffectPatcher(game_path)
            result = patcher.check_effect('Blackberry')
            if result:
                h, desc = result
                self._effect_status.setText(f"Blackberry effect: {desc}")
            else:
                self._effect_status.setText("Could not read Blackberry effect")
        except Exception as e:
            self._effect_status.setText(f"Error: {e}")


    def _buff_ensure_patcher(self) -> bool:
        """Ensure the ItemBuffPatcher is initialised with the current game path."""
        game_path = (self._game_path or self._config.get("game_install_path", "") or "").strip()
        if not game_path:
            QMessageBox.warning(
                self, "No Game Path",
                "Set the game install path at the top of the main window first.",
            )
            return False
        if self._buff_patcher is None or self._buff_patcher.game_path != game_path:
            self._buff_patcher = ItemBuffPatcher(game_path)
        return True


    def _buff_extract_rust(self) -> None:
        """Extract iteminfo using Potter's Rust parser — fastest, full field access."""
        if not self._buff_ensure_patcher():
            return

        if _is_game_running():
            QMessageBox.warning(
                self, "Close Crimson Desert First",
                "Crimson Desert is running — extract cannot read the PAZ while\n"
                "the game has it locked. Close the game and try again.",
            )
            return

        self._buff_status_label.setText("Extracting iteminfo.pabgb (Rust parser)...")
        QApplication.processEvents()

        try:
            import crimson_rs
        except ImportError:
            QMessageBox.critical(self, "Rust Parser",
                "crimson_rs.pyd not found. This requires Potter's Rust parser module.")
            return

        try:
            import time
            raw = self._buff_patcher.extract_iteminfo()
            self._buff_data = bytearray(raw)
            self._buff_modified = False

            if len(raw) < 1000:
                QMessageBox.critical(self, "Extract Failed",
                    f"Extracted data too small ({len(raw)} bytes).\n"
                    f"Game files may be corrupted or modded.")
                return
            import struct as _st
            _magic = _st.unpack_from('<I', raw, 0)[0]
            if _magic == 0 or _magic > 0x10000000:
                QMessageBox.critical(self, "Extract Failed",
                    f"Invalid iteminfo header (0x{_magic:08X}).\n"
                    f"Game files may be corrupted by another mod.\n"
                    f"Try: Steam > Verify Integrity of Game Files")
                return

            t0 = time.perf_counter()
            rust_items = crimson_rs.parse_iteminfo_from_bytes(bytes(raw))
            t1 = time.perf_counter()

            self._buff_rust_items = rust_items
            self._buff_rust_lookup = {it['key']: it for it in rust_items}
            self._buff_use_rust = True
            import copy
            self._buff_rust_items_original = copy.deepcopy(rust_items)

            self._build_effect_catalog(rust_items)

            try:
                from armor_catalog import parse_armor_items
                self._armor_catalog = parse_armor_items(bytes(self._buff_data or b''))
                for a in self._armor_catalog:
                    pretty = self._name_db.get_name(a.item_id)
                    if pretty and not pretty.startswith('Unknown'):
                        a.display_name = pretty
                log.info("Armor catalog: %d items", len(self._armor_catalog))
            except Exception as e:
                log.warning("Armor catalog build failed: %s", e)
                self._armor_catalog = []

            self._buff_status_label.setText(f"Parsed {len(rust_items)} items in {t1-t0:.2f}s. Building offset map...")
            QApplication.processEvents()

            t2 = time.perf_counter()
            self._buff_items = self._buff_patcher.find_items(bytes(self._buff_data))
            t3 = time.perf_counter()

            self._buff_use_structural = True
            self._buff_status_label.setText(
                f"Extracted (Rust): {len(self._buff_data):,} bytes, "
                f"{len(self._buff_items)} items. "
                f"Rust parse: {t1-t0:.2f}s, offset map: {t3-t2:.2f}s. "
                f"Search to find items."
            )
        except Exception as e:
            import traceback; traceback.print_exc()
            self._buff_status_label.setText(f"Rust extraction failed: {e}")
            QMessageBox.warning(self, "Rust Parser Failed",
                f"Failed to parse iteminfo.pabgb:\n{e}\n\n"
                f"Your iteminfo has been modified by another mod or tool.\n"
                f"Restore the original game files before using this feature.\n\n"
                f"Steam > Crimson Desert > Properties > Installed Files > Verify Integrity"
            )
            QMessageBox.critical(self, "Rust Parser Failed", str(e))


    def _buff_extract_iteminfo(self, use_structural: bool = False) -> None:
        """Extract and decompress iteminfo.pabgb from the PAZ archive."""
        if not self._buff_ensure_patcher():
            return

        parser_name = "Potter's parser" if use_structural else "Original scanner"
        self._buff_status_label.setText(f"Extracting iteminfo.pabgb ({parser_name})...")
        QApplication.processEvents()

        try:
            raw = self._buff_patcher.extract_iteminfo()
            self._buff_data = bytearray(raw)
            self._buff_modified = False

            if use_structural:
                self._buff_items = self._buff_patcher.find_items(bytes(self._buff_data))
            else:
                self._buff_items = self._buff_find_items_original(bytes(self._buff_data))

            self._buff_use_structural = use_structural
            self._buff_status_label.setText(
                f"Extracted ({parser_name}): {len(self._buff_data):,} bytes, "
                f"{len(self._buff_items)} items. Use Search to find items."
            )
        except Exception as e:
            self._buff_status_label.setText(f"Extraction failed: {e}")
            QMessageBox.critical(self, "Extraction Failed", str(e))


    def _buff_show_my_inventory(self) -> None:
        """Show items from the loaded save that exist in iteminfo."""
        if self._buff_data is None:
            self._buff_extract_iteminfo(use_structural=False)
            if self._buff_data is None:
                return

        if not self._items:
            QMessageBox.information(self, "No Save Loaded",
                                    "Load a save file first, then click this button.")
            return

        save_keys = set(it.item_key for it in self._items if it.item_key > 0)

        iteminfo_keys = set(it.item_key for it in self._buff_items)

        matching_keys = save_keys & iteminfo_keys

        results = [it for it in self._buff_items if it.item_key in matching_keys and it.name[0:1].isalpha() and it.item_key != 1]

        if not results:
            QMessageBox.information(self, "No Matches",
                                    "No inventory items found in iteminfo database.")
            return

        table = self._buff_items_table
        table.setSortingEnabled(False)
        table.setRowCount(len(results))

        for row, item in enumerate(results):
            icon_cell = QTableWidgetItem()
            if self._buff_icons_enabled:
                px = self._icon_cache.get_pixmap(item.item_key)
                if px:
                    icon_cell.setIcon(QIcon(px))
            table.setItem(row, 0, icon_cell)

            display_name = self._name_db.get_name(item.item_key)
            if display_name.startswith("Unknown"):
                display_name = item.name
            name_cell = QTableWidgetItem(display_name)
            name_cell.setToolTip(f"Internal: {item.name}\nKey: {item.item_key}")
            name_cell.setData(Qt.UserRole, item)
            table.setItem(row, 1, name_cell)

            limits = self._buff_item_limits.get(str(item.item_key), {})
            slot_type = limits.get('slotType', -1)
            if slot_type == 65535 or slot_type == -1:
                type_str = "Item"
            elif slot_type <= 3:
                type_str = "Weapon"
            elif slot_type <= 8:
                type_str = "Armor"
            elif slot_type <= 12:
                type_str = "Accessory"
            else:
                type_str = "Equip"
            table.setItem(row, 2, QTableWidgetItem(type_str))
            table.setItem(row, 3, QTableWidgetItem(str(limits.get('stackLimit', '?'))))

        table.setSortingEnabled(True)
        self._buff_status_label.setText(
            f"Showing {len(results)} items from your inventory that exist in iteminfo "
            f"(out of {len(save_keys)} save items, {len(iteminfo_keys)} iteminfo records)"
        )


    def _buff_search_items(self) -> None:
        """Search for items matching the search text."""
        if self._buff_data is None:
            self._buff_extract_iteminfo(use_structural=False)
            if self._buff_data is None:
                return

        query = self._buff_search.text().strip()
        if not query:
            QMessageBox.information(
                self, "No Search Term",
                "Enter an item name to search for.",
            )
            return

        q = query.lower()
        results = []
        for item in self._buff_items:
            if not item.name[0:1].isalpha():
                continue
            if q in item.name.lower():
                results.append(item)
            elif q in self._name_db.get_name(item.item_key).lower():
                results.append(item)

        table = self._buff_items_table
        table.setSortingEnabled(False)
        table.setRowCount(len(results))

        for row, item in enumerate(results):
            icon_cell = QTableWidgetItem()
            if self._buff_icons_enabled:
                px = self._icon_cache.get_pixmap(item.item_key)
                if px:
                    icon_cell.setIcon(QIcon(px))
            table.setItem(row, 0, icon_cell)

            display_name = self._name_db.get_name(item.item_key)
            if display_name.startswith("Unknown"):
                display_name = item.name
            name_cell = QTableWidgetItem(display_name)
            tip = f"Internal: {item.name}\nKey: {item.item_key}"
            rust_info = self._buff_rust_lookup.get(item.item_key)
            if rust_info:
                edl = rust_info.get('enchant_data_list', [])
                tags = rust_info.get('item_tag_list', [])
                tip += f"\nEquip type: {rust_info.get('equip_type_info', '?')}"
                tip += f"\nCategory: {rust_info.get('category_info', '?')}"
                tip += f"\nTier: {rust_info.get('item_tier', '?')}"
                tip += f"\nEnchant levels: {len(edl)}"
                tip += f"\nMax endurance: {rust_info.get('max_endurance', '?')}"
                tip += f"\nMax sharpness: {rust_info.get('sharpness_data', {}).get('max_sharpness', '?')}"
                if tags:
                    tip += f"\nTags: {', '.join(f'0x{t:X}' for t in tags[:8])}"
            name_cell.setToolTip(tip)
            name_cell.setData(Qt.UserRole, item)
            table.setItem(row, 1, name_cell)

            limits = self._buff_item_limits.get(str(item.item_key), {})
            slot_type = limits.get('slotType', -1)
            if slot_type == 65535 or slot_type == -1:
                type_str = "Item"
            elif slot_type <= 3:
                type_str = "Weapon"
            elif slot_type <= 9:
                type_str = "Armor"
            else:
                type_str = "Equip"
            stack_limit = limits.get('stackLimit', -1)

            type_cell = QTableWidgetItem(type_str)
            if type_str == "Weapon":
                type_cell.setForeground(QBrush(QColor(COLORS['error'])))
            elif type_str in ("Armor", "Equip"):
                type_cell.setForeground(QBrush(QColor(COLORS['accent'])))
            table.setItem(row, 2, type_cell)

            rust_info = self._buff_rust_lookup.get(item.item_key)
            if rust_info:
                tier = rust_info.get('item_tier', 0)
                tier_names = {0: "-", 1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary"}
                tier_cell = QTableWidgetItem(tier_names.get(tier, str(tier)))
                if tier >= 4:
                    tier_cell.setForeground(QBrush(QColor("#c678dd")))
                elif tier == 3:
                    tier_cell.setForeground(QBrush(QColor(COLORS['accent'])))
                table.setItem(row, 3, tier_cell)

                enchant_count = len(rust_info.get('enchant_data_list', []))
                enc_cell = QTableWidgetItem(f"+{enchant_count - 1}" if enchant_count > 1 else "-")
                if enchant_count > 1:
                    enc_cell.setForeground(QBrush(QColor(COLORS['success'])))
                table.setItem(row, 4, enc_cell)
            else:
                table.setItem(row, 3, QTableWidgetItem("-"))
                table.setItem(row, 4, QTableWidgetItem("-"))

            table.setItem(row, 5, QTableWidgetItem(str(stack_limit) if stack_limit > 0 else "\u2014"))

        table.setSortingEnabled(True)

        self._buff_status_label.setText(
            f"Found {len(results)} items matching '{query}'."
        )

        if len(results) == 1:
            table.selectRow(0)
            self._buff_item_selected()
        else:
            self._buff_stats_table.setRowCount(0)
            self._buff_current_item = None
            if hasattr(self, '_buff_selected_label'):
                self._buff_selected_label.setText("No item selected — search and click an item on the left")
                self._buff_selected_label.setStyleSheet(
                    f"color: {COLORS['text_dim']}; font-weight: bold; padding: 2px 4px;"
                )


    def _buff_item_selected(self) -> None:
        """Handle selection of an item in the items table — show its stats."""
        rows = self._buff_items_table.selectionModel().selectedRows()
        if not rows:
            return

        row = rows[0].row()
        name_cell = self._buff_items_table.item(row, 1)
        if name_cell is None:
            return

        item: ItemRecord = name_cell.data(Qt.UserRole)
        if item is None:
            return

        self._buff_current_item = item
        display_name = self._name_db.get_name(item.item_key) if hasattr(self, '_name_db') else item.name
        if display_name.startswith("Unknown"):
            display_name = item.name
        self._buff_selected_label.setText(f"Editing: {display_name}  (key {item.item_key})")
        self._buff_selected_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-weight: bold; padding: 2px 4px;"
        )
        self._buff_refresh_stats()


    def _buff_toggle_icons(self) -> None:
        """Toggle icon display in the ItemBuffs items table."""
        self._buff_icons_enabled = not self._buff_icons_enabled
        if self._buff_icons_enabled:
            self._buff_show_icons_btn.setText("Hide Icons")
            for item in self._buff_items:
                self._icon_cache.get_pixmap(item.item_key)
            row_h = max(ICON_SIZE + 2, 24)
        else:
            self._buff_show_icons_btn.setText("Show Icons")
            row_h = 24

        self._buff_items_table.setColumnWidth(0, (ICON_SIZE + 12) if self._buff_icons_enabled else 0)
        self._buff_items_table.verticalHeader().setDefaultSectionSize(row_h)

        if self._buff_search.text().strip():
            self._buff_search_items()
        elif self._buff_items_table.rowCount() > 0:
            for row in range(self._buff_items_table.rowCount()):
                name_cell = self._buff_items_table.item(row, 1)
                if name_cell:
                    item = name_cell.data(Qt.UserRole)
                    if item and self._buff_icons_enabled:
                        icon_cell = self._buff_items_table.item(row, 0)
                        if icon_cell:
                            px = self._icon_cache.get_pixmap(item.item_key)
                            if px:
                                icon_cell.setIcon(QIcon(px))


    def _buff_refresh_stats(self) -> None:
        """Refresh the stats table — shows Rust dict data as PRIMARY display."""
        item = self._buff_current_item
        if item is None:
            self._buff_stats_table.setRowCount(0)
            return

        if self._buff_data is not None:
            arrays = ItemBuffPatcher.find_stat_arrays(bytes(self._buff_data), item)
            self._buff_current_arrays = arrays
            all_entries = []
            for arr in arrays:
                all_entries.extend(arr.entries)
            item.stat_triplets = all_entries
        else:
            self._buff_current_arrays = []
            item.stat_triplets = []

        table = self._buff_stats_table
        row = 0

        cd_abs_off, cd_val = self._cd_detect(item.item_key)
        patch = self._cd_patches.get(item.item_key) if hasattr(self, '_cd_patches') else None
        if cd_abs_off is not None or patch:
            display_val = patch[2] if patch else cd_val
            orig_val = struct.unpack_from('<I', patch[1])[0] if patch else cd_val
            mins = display_val // 60
            secs = display_val % 60
            time_str = (f"{mins}m {secs}s" if mins else f"{secs}s")
            cd_label = f"Cooldown ({time_str})"
            if patch:
                cd_label += f"  ← was {orig_val}s"
            cd_c1 = QTableWidgetItem(f"  {cd_label}  ← click to edit")
            cd_c1.setForeground(QBrush(QColor("#FFB74D")))
            cd_c1.setFont(QFont("Consolas", 9, QFont.Bold))
            cd_c1.setFlags(cd_c1.flags() & ~Qt.ItemIsSelectable)
            cd_c2 = QTableWidgetItem(f"{display_val:,} s")
            cd_c2.setForeground(QBrush(QColor("#FFB74D")))
            cd_c2.setFont(QFont("Consolas", 10))
            cd_c2.setFlags(cd_c2.flags() & ~Qt.ItemIsSelectable)
            table.setRowCount(row + 1)
            table.setItem(row, 0, cd_c1)
            table.setItem(row, 1, cd_c2)
            row += 1

        _STAT_NAMES = {
            1000000: "HP", 1000001: "Fatal", 1000002: "DDD (Damage)",
            1000003: "DPV (Defense)", 1000004: "DHIT (Accuracy)",
            1000005: "DDV (Base Attack)", 1000006: "Crit Damage",
            1000007: "Crit Rate", 1000008: "Incoming Dmg Rate",
            1000009: "Incoming Dmg Reduction", 1000010: "Attack Speed",
            1000011: "Move Speed", 1000012: "Climb Speed",
            1000013: "Swim Speed", 1000016: "Fire Resist",
            1000017: "Ice Resist", 1000018: "Lightning Resist",
            1000026: "Stamina", 1000027: "MP",
            1000037: "Stamina Cost Reduction", 1000043: "Guard PV Rate",
            1000035: "Max Damage Rate", 1000036: "Pressure",
            1000043: "Guard PV Rate", 1000046: "MP Cost Reduction",
            1000047: "Money Drop Rate", 1000049: "Equip Drop Rate",
            1000050: "DPV Rate",
        }

        rust_info = self._buff_rust_lookup.get(item.item_key) if hasattr(self, '_buff_rust_lookup') else None

        if rust_info:
            edl = rust_info.get('enchant_data_list', [])

            psl = rust_info.get('equip_passive_skill_list', [])
            ps_sep = QTableWidgetItem(f"--- Passive Skills ({len(psl)}) ---")
            ps_sep.setForeground(QBrush(QColor("#4FC3F7")))
            ps_sep.setFont(QFont("Consolas", 9, QFont.Bold))
            ps_sep.setFlags(ps_sep.flags() & ~Qt.ItemIsSelectable)
            table.setRowCount(row + 1)
            table.setItem(row, 0, ps_sep)
            table.setItem(row, 1, QTableWidgetItem(""))
            table.setSpan(row, 0, 1, 2)
            row += 1

            if psl:
                for ps in psl:
                    sk_name = self._PASSIVE_SKILL_NAMES.get(ps['skill'], f"Skill {ps['skill']}")
                    c1 = QTableWidgetItem(f"  {sk_name}  ← click to select for Remove")
                    c1.setForeground(QBrush(QColor("#4FC3F7")))
                    c1.setData(Qt.UserRole + 1, ('passive', ps['skill']))
                    c2 = QTableWidgetItem(f"Lv {ps['level']}")
                    c2.setFont(QFont("Consolas", 10))
                    c2.setForeground(QBrush(QColor("#4FC3F7")))
                    c2.setData(Qt.UserRole + 1, ('passive', ps['skill']))
                    table.setRowCount(row + 1)
                    table.setItem(row, 0, c1)
                    table.setItem(row, 1, c2)
                    row += 1
            else:
                c1 = QTableWidgetItem("  (none)")
                c1.setForeground(QBrush(QColor(COLORS["text_dim"])))
                table.setRowCount(row + 1)
                table.setItem(row, 0, c1)
                table.setItem(row, 1, QTableWidgetItem(""))
                row += 1

            if edl:
                display_level = 0
                if hasattr(self, '_eb_level_target'):
                    sel = self._eb_level_target.currentData()
                    if sel is not None and sel >= 0 and sel < len(edl):
                        display_level = sel

                lvl_hdr = QTableWidgetItem(f"=== Showing Enchant Level +{display_level} (of {len(edl)}) ===")
                lvl_hdr.setForeground(QBrush(QColor(COLORS["warning"])))
                lvl_hdr.setFont(QFont("Consolas", 9, QFont.Bold))
                lvl_hdr.setFlags(lvl_hdr.flags() & ~Qt.ItemIsSelectable)
                table.setRowCount(row + 1)
                table.setItem(row, 0, lvl_hdr)
                table.setItem(row, 1, QTableWidgetItem(""))
                table.setSpan(row, 0, 1, 2)
                row += 1

                ed0 = edl[display_level]
                sd = ed0.get('enchant_stat_data', {})

                for list_name, color, label in [
                    ('stat_list_static', '#FFB74D', 'Flat Stats'),
                    ('stat_list_static_level', '#81C784', 'Rate Stats'),
                    ('regen_stat_list', '#4FC3F7', 'Regen Stats'),
                    ('max_stat_list', '#CE93D8', 'Max Stats'),
                ]:
                    stats = sd.get(list_name, [])
                    sep = QTableWidgetItem(f"--- {label} ({len(stats)}) [level 0/{len(edl)-1}] ---")
                    sep.setForeground(QBrush(QColor(color)))
                    sep.setFont(QFont("Consolas", 9, QFont.Bold))
                    sep.setFlags(sep.flags() & ~Qt.ItemIsSelectable)
                    table.setRowCount(row + 1)
                    table.setItem(row, 0, sep)
                    table.setItem(row, 1, QTableWidgetItem(""))
                    table.setSpan(row, 0, 1, 2)
                    row += 1

                    if stats:
                        for s in stats:
                            sname = (getattr(self, '_STAT_NAMES_COMMUNITY', {}).get(s['stat'])
                                     or _STAT_NAMES.get(s['stat'], f"Stat {s['stat']}"))
                            val = s['change_mb']
                            c1 = QTableWidgetItem(f"  {sname}  ← click to select for Remove")
                            c1.setForeground(QBrush(QColor(color)))
                            c1.setData(Qt.UserRole + 1, ('stat', s['stat'], list_name, val))
                            if 'level' in list_name:
                                c2 = QTableWidgetItem(f"Lv {val}")
                            else:
                                c2 = QTableWidgetItem(f"{val:,}")
                            c2.setFont(QFont("Consolas", 10))
                            c2.setForeground(QBrush(QColor(color)))
                            c2.setData(Qt.UserRole + 1, ('stat', s['stat'], list_name, val))
                            table.setRowCount(row + 1)
                            table.setItem(row, 0, c1)
                            table.setItem(row, 1, c2)
                            row += 1

            all_buffs = []
            if edl:
                all_buffs = edl[0].get('equip_buffs', [])
            eb_sep = QTableWidgetItem(f"--- Equipment Buffs ({len(all_buffs)}) ---")
            eb_sep.setForeground(QBrush(QColor("#AB47BC")))
            eb_sep.setFont(QFont("Consolas", 9, QFont.Bold))
            eb_sep.setFlags(eb_sep.flags() & ~Qt.ItemIsSelectable)
            table.setRowCount(row + 1)
            table.setItem(row, 0, eb_sep)
            table.setItem(row, 1, QTableWidgetItem(""))
            table.setSpan(row, 0, 1, 2)
            row += 1

            for b in all_buffs:
                bname = self._EQUIP_BUFF_NAMES.get(b['buff'], f"Buff {b['buff']}")
                c1 = QTableWidgetItem(f"  {bname}  ← click to select for Remove")
                c1.setForeground(QBrush(QColor("#AB47BC")))
                c1.setData(Qt.UserRole + 1, ('buff', b['buff']))
                c2 = QTableWidgetItem(f"Lv {b['level']}")
                c2.setFont(QFont("Consolas", 10))
                c2.setForeground(QBrush(QColor("#AB47BC")))
                c2.setData(Qt.UserRole + 1, ('buff', b['buff']))
                table.setRowCount(row + 1)
                table.setItem(row, 0, c1)
                table.setItem(row, 1, c2)
                row += 1

            sharp = rust_info.get('sharpness_data', {})
            max_sharp = sharp.get('max_sharpness', 0)
            if max_sharp > 0:
                sep2 = QTableWidgetItem(f"--- Sharpness (max {max_sharp}) ---")
                sep2.setForeground(QBrush(QColor(COLORS["warning"])))
                sep2.setFont(QFont("Consolas", 9, QFont.Bold))
                sep2.setFlags(sep2.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEditable)
                table.setRowCount(row + 1)
                table.setItem(row, 0, sep2)
                table.setItem(row, 1, QTableWidgetItem(""))
                table.setSpan(row, 0, 1, 2)
                row += 1

        display_name = self._name_db.get_name(item.item_key)
        if display_name.startswith("Unknown"):
            display_name = item.name

        n_arrays = len(arrays)
        n_entries = len(all_entries)
        classes = set(e.size_class for e in all_entries)
        class_str = "/".join(sorted(classes)) if classes else "none"

        rust_extra = ""
        if rust_info:
            tier = rust_info.get('item_tier', 0)
            tier_names = {0: "-", 1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary"}
            edl = rust_info.get('enchant_data_list', [])
            rust_extra = f"  |  Tier: {tier_names.get(tier, str(tier))}  |  Enchant levels: {len(edl)}"

        self._buff_status_label.setText(
            f"{display_name}: {n_entries} stats in {n_arrays} arrays [{class_str}]{rust_extra}"
        )

        self._buff_array_combo.blockSignals(True)
        self._buff_array_combo.clear()
        self._buff_array_combo.addItem("All Levels (apply to every array)")
        for i in range(n_arrays):
            class_label = arrays[i].entries[0].size_class if arrays[i].entries else "?"
            self._buff_array_combo.addItem(f"Level {i+1} — Array {i} [{class_label}] ({len(arrays[i].entries)} stats)")
        self._buff_array_combo.blockSignals(False)

    _BUFF_PRESETS = [
        {"name": "Max All"},
        {"name": "Max All Flat"},
        {"name": "Max DDD"},
        {"name": "Max DPV"},
        {"name": "Max HP"},
        {"name": "Max All Rates"},
        {"name": "Swap to DDD"},
        {"name": "Swap to DPV"},
        None,
    ]

    _BUFF_DESCRIPTIONS = {
        "Invincible": "Makes you unkillable. Value: 1 = on, 0 = off. [flat2 — 12B entry]",
        "Hp": "Maximum health points. Value: raw HP number (e.g. 1,000,000). [flat1 — 8B entry]",
        "DDD (Damage)": "Direct Damage Dealt. Attack power. Value: raw damage number. [flat2 — 12B entry]",
        "DPV (Defense)": "Defense Point Value. Damage reduction. Value: raw number. [flat2 — 12B entry]",
        "CriticalDamage": "Extra damage on critical hits. [flat2 — 12B entry, value = raw]",
        "AttackedDamageRate": "Extra damage taken modifier. [flat2 — 12B entry, value = raw]",
        "AttackedDamageReduction": "Damage reduction rate. [flat2 — 12B entry, value = raw]",
        "CriticalRate": "Chance to land a critical hit. Level 0-255 (varies per stat). [rate — 5B entry]",
        "AttackSpeedRate": "How fast you swing/attack. Level 0-255 (varies per stat). [rate — 5B entry]",
        "MoveSpeedRate": "How fast you run. Level 0-255 (varies per stat). [rate — 5B entry]",
        "ClimbSpeedRate": "Climbing speed. Level 0-255 (varies per stat). [rate — 5B entry]",
        "SwimSpeedRate": "Swimming speed. Level 0-255 (varies per stat). [rate — 5B entry]",
        "StaminaRegen": "Stamina recovery rate. Level 0-255 (varies per stat). [rate — 5B entry]",
        "HpRegen": "Health regeneration rate. Level 0-255 (varies per stat). [rate — 5B entry]",
        "MpRegen": "Mana regeneration rate. Level 0-255 (varies per stat). [rate — 5B entry]",
        "FireResistance": "Fire damage resistance. Level 0-255 (varies per stat). [rate — 5B entry]",
        "IceResistance": "Ice damage resistance. Level 0-255 (varies per stat). [rate — 5B entry]",
        "ElectricResistance": "Electric damage resistance. Level 0-255 (varies per stat). [rate — 5B entry]",
        "GuardPVRate": "Guard/block effectiveness. Level 0-255 (varies per stat). [rate — 5B entry]",
        "ReduceCraftMaterial": "Reduces crafting material cost. Level 0-255 (varies per stat). [rate — 5B entry]",
        "MoreOreDrop": "Bonus ore from mining. Level 0-255 (varies per stat). [rate — 5B entry]",
        "MoreLumberDrop": "Bonus lumber from chopping. Level 0-255 (varies per stat). [rate — 5B entry]",
        "EquipDropRate": "Equipment drop rate bonus. Level 0-255 (varies per stat). [rate — 5B entry]",
        "MoneyDropRate": "Silver drop rate bonus. Level 0-255 (varies per stat). [rate — 5B entry]",
        "CollectDrop_Ore": "Collection bonus: ore. Level 0-255 (varies per stat). [rate — 5B entry]",
        "CollectDrop_Plant": "Collection bonus: plants. Level 0-255 (varies per stat). [rate — 5B entry]",
        "CollectDrop_Animal": "Collection bonus: animal parts. Level 0-255 (varies per stat). [rate — 5B entry]",
        "CollectDrop_Log": "Collection bonus: logs. Level 0-255 (varies per stat). [rate — 5B entry]",
    }

    _PRESET_DESCRIPTIONS = [
        "Max every stat on the item: flat values to 999,999, rate levels to 15. No hash changes — keeps original stat types.",
        "Max all flat stats: sets all flat2/flat1 values to 999,999 at every refinement level.",
        "Max DDD to 999,999 at every refinement level. Only edits flat2 stat entries.",
        "Max DPV to 999,999 at every refinement level. Only edits flat2 stat entries.",
        "Max HP to 999,999 at every refinement level. Only edits flat1 stat entries.",
        "Set all rate stats to Lv 15 (max). Only edits rate entries.",
        "Swap existing flat2 stat to DDD (Damage). Same size class, safe in-place swap.",
        "Swap existing flat2 stat to DPV (Defense). Same size class, safe in-place swap.",
        "",
    ]


    def _buff_update_desc(self, *_args) -> None:
        """Update the description label based on current selection."""
        if not hasattr(self, '_buff_desc_label'):
            return
        idx = self._buff_preset_combo.currentIndex()
        if idx < len(self._PRESET_DESCRIPTIONS) - 1:
            self._buff_desc_label.setText(self._PRESET_DESCRIPTIONS[idx])
        else:
            stat_name = self._buff_type_combo.currentText()
            desc = self._BUFF_DESCRIPTIONS.get(stat_name, "")
            self._buff_desc_label.setText(desc)


    def _buff_preset_changed(self, index: int) -> None:
        """Show/hide custom controls based on preset selection."""
        is_custom = (index == len(self._BUFF_PRESETS) - 1)
        self._buff_custom_row.setVisible(is_custom)


    def _eb_apply(self) -> None:
        """Change the passive skill on the selected item (resistance, element, etc.)."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Passive Editor",
                "Extract with Rust parser first (click 'Extract (Rust)').")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Passive Editor", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "Passive Editor", "Item not found in Rust data.")
            return

        psl = rust_info.get('equip_passive_skill_list', [])

        new_skill = self._eb_passive_combo.currentData()
        new_level = self._eb_level_spin.value()
        new_name = self._PASSIVE_SKILL_NAMES.get(new_skill, f"Skill {new_skill}")

        already_has = any(p['skill'] == new_skill for p in psl)

        current_list = ", ".join(
            f"{self._PASSIVE_SKILL_NAMES.get(p['skill'], p['skill'])} Lv{p['level']}"
            for p in psl) or "(none)"

        if already_has:
            msg = (f"Update passive level on this item:\n\n"
                   f"  {new_name}: update to Lv {new_level}\n"
                   f"  Current passives: {current_list}")
        else:
            msg = (f"ADD passive to this item:\n\n"
                   f"  Adding: {new_name} Lv {new_level}\n"
                   f"  Current passives: {current_list}\n\n"
                   f"This will ADD to existing passives (not replace).")

        reply = QMessageBox.question(
            self, "Add Passive Skill",
            f"{msg}\n\n"
            f"Click 'Export as Mod' after to write.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if already_has:
            for p in psl:
                if p['skill'] == new_skill:
                    p['level'] = new_level
                    break
        else:
            psl.append({'skill': new_skill, 'level': new_level})
            rust_info['equip_passive_skill_list'] = psl

        self._buff_modified = True
        total = len(rust_info.get('equip_passive_skill_list', []))
        self._eb_status.setText(f"Added {new_name} Lv{new_level} ({total} passives) — click Export as Mod")
        self._buff_refresh_stats()


    def _eb_add_stat(self) -> None:
        """Add an enchant stat to ALL enchant levels of the selected item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Add Stat", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Add Stat", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            QMessageBox.warning(self, "Add Stat", "This item has no enchant data.")
            return

        combo_idx = self._eb_stat_combo.currentData()
        if combo_idx is None or combo_idx >= len(self._ENCHANT_STAT_LIST):
            return
        stat_name, stat_key, stat_list, _ = self._ENCHANT_STAT_LIST[combo_idx]
        stat_value = self._eb_stat_value.value()

        target_level = self._eb_level_target.currentData()

        added = 0
        for idx, ed in enumerate(edl):
            if target_level != -1 and idx != target_level:
                continue
            sd = ed.setdefault('enchant_stat_data', {})
            existing = sd.get(stat_list, [])
            replaced = False
            for i, s in enumerate(existing):
                if s['stat'] == stat_key:
                    existing[i] = {'stat': stat_key, 'change_mb': stat_value}
                    replaced = True
                    break
            if not replaced:
                existing.append({'stat': stat_key, 'change_mb': stat_value})
            sd[stat_list] = existing
            added += 1

        self._buff_modified = True
        self._buff_refresh_stats()
        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        level_str = f"level +{target_level}" if target_level >= 0 else f"{added} levels"
        self._buff_status_label.setText(
            f"Added {stat_name}={stat_value:,} to {display_name} ({level_str}). "
            f"Click 'Export as Mod' to write.")


    def _eb_remove_stat(self) -> None:
        """Remove an enchant stat from ALL enchant levels."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            return

        combo_idx = self._eb_stat_combo.currentData()
        if combo_idx is None or combo_idx >= len(self._ENCHANT_STAT_LIST):
            return
        stat_name, stat_key, stat_list, _ = self._ENCHANT_STAT_LIST[combo_idx]

        removed = 0
        for ed in edl:
            sd = ed.get('enchant_stat_data', {})
            existing = sd.get(stat_list, [])
            new_list = [s for s in existing if s['stat'] != stat_key]
            if len(new_list) < len(existing):
                removed += 1
            sd[stat_list] = new_list

        if removed == 0:
            QMessageBox.information(self, "Remove Stat", f"{stat_name} not found on this item.")
            return

        self._buff_modified = True
        self._buff_refresh_stats()
        self._buff_status_label.setText(f"Removed {stat_name} from {removed} enchant levels.")


    def _eb_json_edit(self) -> None:
        """Open raw enchant data as editable JSON for power users."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "JSON Edit", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "JSON Edit", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        display_name = self._name_db.get_name(self._buff_current_item.item_key)

        edit_data = {
            "item_key": rust_info['key'],
            "string_key": rust_info.get('string_key', ''),
            "equip_passive_skill_list": rust_info.get('equip_passive_skill_list', []),
            "gimmick_info": rust_info.get('gimmick_info', 0),
            "cooltime": rust_info.get('cooltime', 0),
            "item_charge_type": rust_info.get('item_charge_type', 0),
            "max_charged_useable_count": rust_info.get('max_charged_useable_count', 0),
            "respawn_time_seconds": rust_info.get('respawn_time_seconds', 0),
        }
        dcd = rust_info.get('docking_child_data')
        if dcd:
            edit_data["docking_child_data"] = dcd
        else:
            edit_data["docking_child_data"] = {
                "_note": "DELETE this _note field. Fill in gimmick_info_key to enable item activation.",
                "gimmick_info_key": 0,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [0, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 1,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            }

        ddd = rust_info.get('drop_default_data')
        if ddd:
            edit_data["drop_default_data"] = {
                "drop_enchant_level": ddd.get('drop_enchant_level', 0),
                "socket_item_list": ddd.get('socket_item_list', []),
                "add_socket_material_item_list": ddd.get('add_socket_material_item_list', []),
                "socket_valid_count": ddd.get('socket_valid_count', 0),
                "use_socket": ddd.get('use_socket', 0),
            }

        edl = rust_info.get('enchant_data_list', [])
        if edl:
            ed0 = edl[0]
            edit_data["enchant_stat_data"] = ed0.get('enchant_stat_data', {})
            edit_data["equip_buffs"] = ed0.get('equip_buffs', [])
            edit_data["_note"] = f"Edits apply to ALL {len(edl)} enchant levels"

        json_text = json.dumps(edit_data, indent=2, ensure_ascii=False)

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Advanced JSON Edit: {display_name}")
        dlg.resize(650, 550)
        dl = QVBoxLayout(dlg)

        info = QLabel(
            "Edit the JSON below. Changes to enchant_stat_data and equip_buffs\n"
            "will be applied to ALL enchant levels. Click Apply to save.\n\n"
            "Stat keys: 1000002=DDD, 1000003=DPV, 1000007=CritRate, 1000010=AtkSpeed,\n"
            "1000011=MoveSpeed, 1000024=FireRes, 1000025=IceRes, 1000026=LightningRes\n\n"
            "Gimmick: Set gimmick_info + docking_child_data.gimmick_info_key to same value.\n"
            "cooltime >= 1 (0 crashes). item_charge_type: 0=activated, 2=passive.\n"
            "Lightning (gimmick 1001961) = pure VFX. Works on twohand/hammer/spear/glove;\n"
            "one-handed gets visual only (skill has weapon-type filter).\n\n"
            "drop_default_data: add entries to add_socket_material_item_list to grant\n"
            "more sockets. Length of list = max socket count."
        )
        info.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 11px; padding: 4px;")
        dl.addWidget(info)

        text_edit = QTextEdit()
        text_edit.setFont(QFont("Consolas", 10))
        text_edit.setPlainText(json_text)
        dl.addWidget(text_edit, 1)

        btn_row = QHBoxLayout()
        apply_btn = QPushButton("Apply Changes")
        apply_btn.setObjectName("accentBtn")
        apply_btn.setStyleSheet("background-color: #cc3333; color: white; font-weight: bold;")
        btn_row.addWidget(apply_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch()
        dl.addLayout(btn_row)

        def _apply():
            try:
                new_data = json.loads(text_edit.toPlainText())
            except json.JSONDecodeError as e:
                QMessageBox.warning(dlg, "Invalid JSON", f"Parse error:\n{e}")
                return

            if 'equip_passive_skill_list' in new_data:
                rust_info['equip_passive_skill_list'] = new_data['equip_passive_skill_list']

            for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                        'max_charged_useable_count', 'docking_child_data',
                        'respawn_time_seconds'):
                if gf in new_data:
                    val = new_data[gf]
                    if isinstance(val, dict):
                        val = {k: v for k, v in val.items() if not k.startswith('_note')}
                    if gf == 'docking_child_data' and isinstance(val, dict):
                        if val.get('gimmick_info_key', 0) == 0:
                            continue
                    rust_info[gf] = val

            if 'drop_default_data' in new_data:
                new_ddd = new_data['drop_default_data']
                cur_ddd = rust_info.get('drop_default_data')
                if cur_ddd and isinstance(new_ddd, dict):
                    for k in ('drop_enchant_level', 'socket_item_list',
                              'add_socket_material_item_list', 'socket_valid_count',
                              'use_socket'):
                        if k in new_ddd:
                            cur_ddd[k] = new_ddd[k]

            edl = rust_info.get('enchant_data_list', [])
            if 'enchant_stat_data' in new_data:
                for ed in edl:
                    ed['enchant_stat_data'] = json.loads(json.dumps(new_data['enchant_stat_data']))
            if 'equip_buffs' in new_data:
                for ed in edl:
                    ed['equip_buffs'] = json.loads(json.dumps(new_data['equip_buffs']))

            self._buff_modified = True
            self._buff_refresh_stats()
            dlg.accept()
            self._buff_status_label.setText(f"Applied JSON edits to {display_name}. Click 'Export as Mod'.")

        apply_btn.clicked.connect(_apply)
        dlg.exec()


    def _apply_transmog_swaps(self, final_data: bytearray) -> int:
        """Apply queued transmog byte patches to a serialized iteminfo blob.

        Re-derives hash offsets from the current serialized blob so we're safe
        against rust serialize shifting things. Called from all export paths
        after rust serialize + cooldown patches, before pack_mod.

        Returns the number of byte patches applied.
        """
        if not getattr(self, '_transmog_swaps', None):
            return 0
        try:
            from armor_catalog import apply_swaps_to_blob
            applied = apply_swaps_to_blob(final_data, self._transmog_swaps)
            log.info("Transmog: applied %d byte patches for %d queued swap(s)",
                     applied, len(self._transmog_swaps))
            if applied == 0 and self._transmog_swaps:
                try:
                    QMessageBox.warning(
                        self, "Transmog Not Applied",
                        "Transmog swaps were queued but 0 byte patches landed.\n\n"
                        "Likely cause: heavy ItemBuffs edits (many equip_buffs or\n"
                        "passives) expanded item records so the transmog catalog\n"
                        "lost the target items during re-parse.\n\n"
                        "Check the log for 'apply_swaps_to_blob' warnings, then\n"
                        "reduce buff stacks or report with the log attached.",
                    )
                except Exception:
                    pass
            return applied
        except Exception as e:
            log.warning("Transmog apply failed: %s", e)
            return 0


    def _apply_vfx_changes(self, final_data: bytearray) -> bool:
        """Apply queued VFX Lab edits to the serialized iteminfo blob.

        Uses crimson_rs structured parse→mutate→serialize so size changes and
        longer socket names are handled natively. Mutates final_data in place
        by reassigning contents. Returns True if anything was applied.
        """
        if not (getattr(self, '_vfx_size_changes', None) or
                getattr(self, '_vfx_swaps', None) or
                getattr(self, '_vfx_anim_swaps', None) or
                getattr(self, '_vfx_attach_changes', None)):
            return False
        if not getattr(self, '_experimental_mode', False):
            log.info("VFX Lab changes queued but skipped — experimental mode is off")
            return False
        try:
            import vfx_lab
            new_bytes = vfx_lab.apply_all_changes(
                bytes(final_data),
                self._vfx_size_changes,
                self._vfx_swaps,
                self._vfx_anim_swaps,
                self._vfx_attach_changes,
            )
            final_data.clear()
            final_data.extend(new_bytes)
            log.info("VFX Lab: applied %d size, %d vfx, %d anim, %d attach",
                     len(self._vfx_size_changes), len(self._vfx_swaps),
                     len(self._vfx_anim_swaps), len(self._vfx_attach_changes))
            return True
        except Exception as e:
            log.warning("VFX Lab apply failed: %s", e)
            return False


    def _buff_open_vfx_dialog(self) -> None:
        """Open the VFX Lab dialog (4 tabs: Size / VFX / Anim / Attach)."""
        if self._buff_data is None:
            QMessageBox.information(self, "VFX Lab",
                "Click 'Extract' first to load iteminfo data.")
            return
        try:
            import vfx_lab
        except ImportError as e:
            QMessageBox.warning(self, "VFX Lab", f"Module load failed: {e}")
            return

        if not self._vfx_summaries:
            try:
                summaries, _raw = vfx_lab.parse_vfx_catalog(bytes(self._buff_data))
                try:
                    from armor_catalog import get_category, clean_display_name
                except Exception:
                    get_category = lambda n: None
                    clean_display_name = lambda n: n
                name_db = getattr(self, '_name_db', None)
                for s in summaries:
                    cat = get_category(s.internal_name) or "Other"
                    s.category = cat
                    disp = ''
                    if name_db:
                        try:
                            disp = name_db.get_name(s.item_key) or ''
                        except Exception:
                            disp = ''
                    s.display_name = disp or clean_display_name(s.internal_name)
                self._vfx_summaries = summaries
            except Exception as e:
                QMessageBox.warning(self, "VFX Lab", f"Parse failed: {e}")
                return

        self._vfx_show_dialog(vfx_lab)


    def _vfx_show_dialog(self, vfx_lab) -> None:
        """Construct and show the VFX Lab tabbed dialog."""
        dlg = QDialog(self)
        dlg.setWindowTitle("VFX Lab — Size / VFX / Animations / Attach Points")
        dlg.resize(1100, 720)
        lay = QVBoxLayout(dlg)
        banner = QLabel(
            "Edit item visuals directly in iteminfo.pabgb. "
            "Size & VFX are safe; Animation and Attach can crash if sockets/rigs mismatch.")
        banner.setWordWrap(True)
        banner.setStyleSheet("background: #263238; color: #B0BEC5; padding: 6px; border-radius: 4px;")
        lay.addWidget(banner)

        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        summaries = self._vfx_summaries
        by_key = {s.item_key: s for s in summaries}

        owned_keys: set[int] = set()
        try:
            for rec in (self._buff_items or []):
                k = getattr(rec, 'key', None) or getattr(rec, 'item_key', None)
                if k is not None:
                    owned_keys.add(int(k))
        except Exception:
            pass

        local_size = list(self._vfx_size_changes)
        local_vfx = list(self._vfx_swaps)
        local_anim = list(self._vfx_anim_swaps)
        local_attach = list(self._vfx_attach_changes)

        tabs.addTab(self._vfx_build_size_tab(vfx_lab, summaries, by_key, owned_keys, local_size), "Size")
        tabs.addTab(self._vfx_build_vfx_tab(vfx_lab, summaries, by_key, owned_keys, local_vfx), "VFX & Trails")
        tabs.addTab(self._vfx_build_anim_tab(vfx_lab, summaries, by_key, owned_keys, local_anim), "Animations")
        tabs.addTab(self._vfx_build_attach_tab(vfx_lab, summaries, by_key, owned_keys, local_attach), "Attach Points")

        footer = QHBoxLayout()
        apply_btn = QPushButton("Apply All to Queue")
        apply_btn.setStyleSheet("background: #2E7D32; color: white; font-weight: bold; padding: 6px 14px;")
        clear_btn = QPushButton("Clear All VFX Changes")
        clear_btn.setStyleSheet("background: #6A1B1B; color: white; padding: 6px 14px;")
        import_btn = QPushButton("Import JSON…")
        export_btn = QPushButton("Export JSON…")
        close_btn = QPushButton("Close")
        for b in (apply_btn, clear_btn, import_btn, export_btn):
            footer.addWidget(b)
        footer.addStretch()
        footer.addWidget(close_btn)
        lay.addLayout(footer)

        def on_apply():
            self._vfx_size_changes = list(local_size)
            self._vfx_swaps = list(local_vfx)
            self._vfx_anim_swaps = list(local_anim)
            self._vfx_attach_changes = list(local_attach)
            total = len(local_size) + len(local_vfx) + len(local_anim) + len(local_attach)
            self._buff_modified = self._buff_modified or total > 0
            self._buff_status_label.setText(
                f"VFX Lab: {len(local_size)} size, {len(local_vfx)} vfx, "
                f"{len(local_anim)} anim, {len(local_attach)} attach queued. "
                "Click 'Export as Mod' when ready.")
            dlg.accept()

        def on_clear():
            if QMessageBox.question(dlg, "Clear VFX Lab",
                    "Remove all queued size / VFX / animation / attach changes?",
                    QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            local_size.clear(); local_vfx.clear(); local_anim.clear(); local_attach.clear()
            self._vfx_size_changes = []; self._vfx_swaps = []
            self._vfx_anim_swaps = []; self._vfx_attach_changes = []
            QMessageBox.information(dlg, "VFX Lab", "All queued changes cleared. Close and reopen to refresh lists.")

        def on_import():
            path, _ = QFileDialog.getOpenFileName(dlg, "Import VFX Lab JSON", "", "JSON (*.json)")
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    text = f.read()
                s, v, a, at = vfx_lab.import_changes_from_json(text)
                local_size.extend(s); local_vfx.extend(v)
                local_anim.extend(a); local_attach.extend(at)
                QMessageBox.information(dlg, "Import",
                    f"Imported {len(s)} size, {len(v)} vfx, {len(a)} anim, {len(at)} attach entries.\n"
                    "Click Apply All to Queue, then Export as Mod.")
            except Exception as e:
                QMessageBox.warning(dlg, "Import failed", str(e))

        def on_export():
            if not (local_size or local_vfx or local_anim or local_attach):
                QMessageBox.information(dlg, "Export", "Nothing to export.")
                return
            path, _ = QFileDialog.getSaveFileName(dlg, "Export VFX Lab JSON", "vfx_lab.json", "JSON (*.json)")
            if not path:
                return
            try:
                text = vfx_lab.export_changes_to_json(
                    local_size, local_vfx, local_anim, local_attach, by_key)
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(text)
                QMessageBox.information(dlg, "Export", f"Wrote {path}")
            except Exception as e:
                QMessageBox.warning(dlg, "Export failed", str(e))

        apply_btn.clicked.connect(on_apply)
        clear_btn.clicked.connect(on_clear)
        import_btn.clicked.connect(on_import)
        export_btn.clicked.connect(on_export)
        close_btn.clicked.connect(dlg.reject)

        dlg.exec()


    def _vfx_build_item_filter_widgets(self, summaries, owned_keys, show_owned_toggle: bool):
        """Shared list-pane widgets with search + owned filter."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 4, 4, 4)
        ctl = QHBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText("Search…")
        ctl.addWidget(search, 1)
        cat_combo = QComboBox()
        cats = sorted({s.category for s in summaries if s.category}) or ["Other"]
        cat_combo.addItem("(all)")
        for c in cats:
            cat_combo.addItem(c)
        ctl.addWidget(cat_combo)
        owned_cb = QCheckBox("Only items I own")
        owned_cb.setChecked(False)
        if show_owned_toggle:
            ctl.addWidget(owned_cb)
        lay.addLayout(ctl)
        lst = QListWidget()
        lay.addWidget(lst, 1)
        return w, lst, search, cat_combo, owned_cb


    def _vfx_filter_match(self, s, q: str, cat: str, owned_only: bool, owned_keys: set) -> bool:
        if owned_only and s.item_key not in owned_keys:
            return False
        if cat and cat != "(all)" and s.category != cat:
            return False
        if q:
            ql = q.lower()
            if ql not in s.internal_name.lower() and ql not in (s.display_name or '').lower():
                return False
        return True


    def _vfx_populate_list(self, lst, summaries, filter_fn, label_fn):
        lst.clear()
        for s in summaries:
            if not filter_fn(s):
                continue
            it = QListWidgetItem(label_fn(s))
            it.setData(Qt.UserRole, s.item_key)
            lst.addItem(it)


    def _vfx_build_size_tab(self, vfx_lab, summaries, by_key, owned_keys, queue):
        tab = QWidget()
        root = QHBoxLayout(tab)

        left_w, lst, search, cat_combo, owned_cb = self._vfx_build_item_filter_widgets(
            summaries, owned_keys, show_owned_toggle=True)
        root.addWidget(left_w, 2)

        right = QWidget()
        rlay = QVBoxLayout(right)
        cur_lbl = QLabel("Select an item on the left.")
        cur_lbl.setWordWrap(True)
        rlay.addWidget(cur_lbl)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(10, 500)
        slider.setValue(100)
        slider_lbl = QLabel("Scale: 1.00×")
        rlay.addWidget(slider_lbl)
        rlay.addWidget(slider)

        uniform_cb = QCheckBox("Uniform (lock X/Y/Z)")
        uniform_cb.setChecked(True)
        rlay.addWidget(uniform_cb)

        queue_list = QListWidget()
        rlay.addWidget(QLabel("Queued size changes:"))
        rlay.addWidget(queue_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add / Update")
        add_btn.setStyleSheet("background: #2E7D32; color: white; font-weight: bold;")
        rm_btn = QPushButton("Remove Selected")
        btn_row.addWidget(add_btn); btn_row.addWidget(rm_btn)
        rlay.addLayout(btn_row)
        root.addWidget(right, 3)

        def refresh_list():
            q = search.text().strip()
            cat = cat_combo.currentText()
            owned = owned_cb.isChecked()
            self._vfx_populate_list(
                lst, summaries,
                lambda s: bool(s.scale) and self._vfx_filter_match(s, q, cat, owned, owned_keys),
                lambda s: f"{s.display_name or s.internal_name}  [{s.category}]  cur:{s.scale}")

        def refresh_queue():
            queue_list.clear()
            for ch in queue:
                s = by_key.get(ch.item_key)
                nm = (s.display_name or s.internal_name) if s else f"key={ch.item_key}"
                queue_list.addItem(f"{nm}  →  scale {ch.scale}")

        def on_select():
            it = lst.currentItem()
            if not it:
                cur_lbl.setText("Select an item on the left.")
                return
            s = by_key.get(it.data(Qt.UserRole))
            if not s:
                return
            cur_lbl.setText(f"{s.display_name or s.internal_name}\nCurrent scale: {s.scale}")
            ex = next((c for c in queue if c.item_key == s.item_key), None)
            if ex and ex.scale:
                slider.setValue(int(round(ex.scale[0] * 100)))
            else:
                slider.setValue(100)

        def on_slider(v):
            slider_lbl.setText(f"Scale: {v/100:.2f}×")

        def on_add():
            it = lst.currentItem()
            if not it:
                QMessageBox.information(tab, "Size", "Pick an item first.")
                return
            s = by_key.get(it.data(Qt.UserRole))
            if not s or not s.scale:
                return
            v = slider.value() / 100.0
            new_scale = [v] * len(s.scale)
            queue[:] = [c for c in queue if c.item_key != s.item_key]
            queue.append(vfx_lab.SizeChange(item_key=s.item_key, gv_index=0, scale=new_scale))
            refresh_queue()

        def on_remove():
            row = queue_list.currentRow()
            if 0 <= row < len(queue):
                del queue[row]
                refresh_queue()

        search.textChanged.connect(lambda _: refresh_list())
        cat_combo.currentTextChanged.connect(lambda _: refresh_list())
        owned_cb.stateChanged.connect(lambda _: refresh_list())
        lst.currentItemChanged.connect(lambda *_: on_select())
        slider.valueChanged.connect(on_slider)
        add_btn.clicked.connect(on_add)
        rm_btn.clicked.connect(on_remove)

        refresh_list()
        refresh_queue()
        return tab


    def _vfx_build_vfx_tab(self, vfx_lab, summaries, by_key, owned_keys, queue):
        tab = QWidget()
        root = QVBoxLayout(tab)
        hint = QLabel(
            "Copy a source item's VFX prefabs (trails, glows, particle systems) onto your target. "
            "Positions [1], [3], [4] are typically trails/auras; position [0] is the mesh "
            "(handled by Transmog).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #90A4AE; padding: 4px;")
        root.addWidget(hint)

        split = QHBoxLayout()
        root.addLayout(split, 1)

        tgt_w, tgt_lst, tgt_search, tgt_cat, tgt_owned = self._vfx_build_item_filter_widgets(
            summaries, owned_keys, show_owned_toggle=True)
        tgt_owned.setChecked(True)
        ltw = QWidget(); llay = QVBoxLayout(ltw)
        llay.addWidget(QLabel("<b>YOUR EQUIPMENT (target)</b>"))
        llay.addWidget(tgt_w)
        split.addWidget(ltw, 2)

        src_w, src_lst, src_search, src_cat, src_owned = self._vfx_build_item_filter_widgets(
            summaries, owned_keys, show_owned_toggle=False)
        rtw = QWidget(); rlay = QVBoxLayout(rtw)
        rlay.addWidget(QLabel("<b>NEW VFX (source)</b>"))
        rlay.addWidget(src_w)
        split.addWidget(rtw, 2)

        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Prefab positions:"))
        pos_checks = []
        for i in (0, 1, 2, 3, 4, 5):
            cb = QCheckBox(f"[{i}]")
            cb.setChecked(i == 0)
            pos_checks.append((i, cb))
            pos_row.addWidget(cb)
        pos_row.addStretch()
        root.addLayout(pos_row)

        queue_list = QListWidget()
        root.addWidget(QLabel("Queued VFX swaps:"))
        root.addWidget(queue_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Queue Swap")
        add_btn.setStyleSheet("background: #2E7D32; color: white; font-weight: bold;")
        rm_btn = QPushButton("Remove Selected")
        btn_row.addWidget(add_btn); btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        root.addLayout(btn_row)

        def refresh_lists():
            for (q, cat, owned, lst) in (
                (tgt_search.text().strip(), tgt_cat.currentText(), tgt_owned.isChecked(), tgt_lst),
                (src_search.text().strip(), src_cat.currentText(), src_owned.isChecked(), src_lst)):
                self._vfx_populate_list(
                    lst, summaries,
                    lambda s, q=q, cat=cat, owned=owned: (
                        bool(s.prefab_names) and
                        self._vfx_filter_match(s, q, cat, owned, owned_keys)),
                    lambda s: f"{s.display_name or s.internal_name}  [{s.category}]  [{len(s.prefab_names)} prefab]")

        def refresh_queue():
            queue_list.clear()
            for sw in queue:
                tn = by_key.get(sw.tgt_key); sn = by_key.get(sw.src_key)
                tnm = (tn.display_name or tn.internal_name) if tn else sw.tgt_key
                snm = (sn.display_name or sn.internal_name) if sn else sw.src_key
                queue_list.addItem(f"{tnm}  ←  {snm}   pos={sw.positions}")

        def on_add():
            ti = tgt_lst.currentItem(); si = src_lst.currentItem()
            if not ti or not si:
                QMessageBox.information(tab, "VFX", "Select one item in each list.")
                return
            tgt_key = ti.data(Qt.UserRole); src_key = si.data(Qt.UserRole)
            if tgt_key == src_key:
                return
            positions = [i for (i, cb) in pos_checks if cb.isChecked()]
            if not positions:
                QMessageBox.information(tab, "VFX", "Check at least one position.")
                return
            queue[:] = [s for s in queue if s.tgt_key != tgt_key]
            queue.append(vfx_lab.VfxSwap(tgt_key=tgt_key, src_key=src_key, gv_index=0, positions=positions))
            refresh_queue()

        def on_remove():
            row = queue_list.currentRow()
            if 0 <= row < len(queue):
                del queue[row]
                refresh_queue()

        for s in (tgt_search, src_search):
            s.textChanged.connect(lambda _: refresh_lists())
        for c in (tgt_cat, src_cat):
            c.currentTextChanged.connect(lambda _: refresh_lists())
        for o in (tgt_owned, src_owned):
            o.stateChanged.connect(lambda _: refresh_lists())
        add_btn.clicked.connect(on_add)
        rm_btn.clicked.connect(on_remove)
        refresh_lists(); refresh_queue()
        return tab


    def _vfx_build_anim_tab(self, vfx_lab, summaries, by_key, owned_keys, queue):
        tab = QWidget()
        root = QVBoxLayout(tab)
        warn = QLabel(
            "⚠ EXPERIMENTAL — Animation swaps can t-pose items or crash the game "
            "if source and target aren't rig-compatible. Only 67 vanilla items have "
            "animation data (mostly recipe books). Test each swap in a throwaway save.")
        warn.setWordWrap(True)
        warn.setStyleSheet("background: #4E342E; color: #FFB74D; padding: 6px; border-radius: 4px;")
        root.addWidget(warn)

        split = QHBoxLayout(); root.addLayout(split, 1)

        tgt_w, tgt_lst, tgt_search, tgt_cat, tgt_owned = self._vfx_build_item_filter_widgets(
            summaries, owned_keys, show_owned_toggle=True)
        tgt_owned.setChecked(True)
        ltw = QWidget(); llay = QVBoxLayout(ltw)
        llay.addWidget(QLabel("<b>YOUR ITEM (target)</b>"))
        llay.addWidget(tgt_w)
        split.addWidget(ltw, 2)

        src_w, src_lst, src_search, src_cat, src_owned = self._vfx_build_item_filter_widgets(
            summaries, owned_keys, show_owned_toggle=False)
        rtw = QWidget(); rlay = QVBoxLayout(rtw)
        rlay.addWidget(QLabel("<b>SOURCE ANIMATIONS (items with anim data)</b>"))
        rlay.addWidget(src_w)
        split.addWidget(rtw, 2)

        queue_list = QListWidget()
        root.addWidget(QLabel("Queued animation swaps:"))
        root.addWidget(queue_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Queue Animation Swap")
        add_btn.setStyleSheet("background: #F9A825; color: black; font-weight: bold;")
        rm_btn = QPushButton("Remove Selected")
        btn_row.addWidget(add_btn); btn_row.addWidget(rm_btn); btn_row.addStretch()
        root.addLayout(btn_row)

        def refresh_lists():
            self._vfx_populate_list(
                tgt_lst, summaries,
                lambda s: bool(s.prefab_names) and self._vfx_filter_match(
                    s, tgt_search.text().strip(), tgt_cat.currentText(), tgt_owned.isChecked(), owned_keys),
                lambda s: f"{s.display_name or s.internal_name}  [{s.category}]  anim:{len(s.animation_path_list)}")
            self._vfx_populate_list(
                src_lst, summaries,
                lambda s: bool(s.animation_path_list) and self._vfx_filter_match(
                    s, src_search.text().strip(), src_cat.currentText(), src_owned.isChecked(), owned_keys),
                lambda s: f"{s.display_name or s.internal_name}  [{s.category}]  anim:{s.animation_path_list}")

        def refresh_queue():
            queue_list.clear()
            for sw in queue:
                tn = by_key.get(sw.tgt_key); sn = by_key.get(sw.src_key)
                tnm = (tn.display_name or tn.internal_name) if tn else sw.tgt_key
                snm = (sn.display_name or sn.internal_name) if sn else sw.src_key
                queue_list.addItem(f"{tnm}  ←  {snm} (anim override)")

        def on_add():
            ti = tgt_lst.currentItem(); si = src_lst.currentItem()
            if not ti or not si:
                return
            tgt_key = ti.data(Qt.UserRole); src_key = si.data(Qt.UserRole)
            if tgt_key == src_key:
                return
            queue[:] = [s for s in queue if s.tgt_key != tgt_key]
            queue.append(vfx_lab.AnimSwap(tgt_key=tgt_key, src_key=src_key, gv_index=0))
            refresh_queue()

        def on_remove():
            row = queue_list.currentRow()
            if 0 <= row < len(queue):
                del queue[row]
                refresh_queue()

        for s in (tgt_search, src_search):
            s.textChanged.connect(lambda _: refresh_lists())
        for c in (tgt_cat, src_cat):
            c.currentTextChanged.connect(lambda _: refresh_lists())
        for o in (tgt_owned, src_owned):
            o.stateChanged.connect(lambda _: refresh_lists())
        add_btn.clicked.connect(on_add)
        rm_btn.clicked.connect(on_remove)
        refresh_lists(); refresh_queue()
        return tab


    def _vfx_build_attach_tab(self, vfx_lab, summaries, by_key, owned_keys, queue):
        tab = QWidget()
        root = QVBoxLayout(tab)
        warn = QLabel(
            "⚠ EXPERIMENTAL — Changes where an item attaches on your character. "
            "Only items with existing dock data can be edited here (261 of 6024). "
            "Unknown socket names render the item invisible — stick to the whitelist.")
        warn.setWordWrap(True)
        warn.setStyleSheet("background: #4E342E; color: #FFB74D; padding: 6px; border-radius: 4px;")
        root.addWidget(warn)

        split = QHBoxLayout(); root.addLayout(split, 2)

        left_w, lst, search, cat_combo, owned_cb = self._vfx_build_item_filter_widgets(
            summaries, owned_keys, show_owned_toggle=True)
        owned_cb.setChecked(True)
        split.addWidget(left_w, 2)

        right = QWidget(); rlay = QVBoxLayout(right)
        cur_lbl = QLabel("Select a dockable item on the left.")
        cur_lbl.setWordWrap(True)
        rlay.addWidget(cur_lbl)

        rlay.addWidget(QLabel("New parent socket:"))
        socket_combo = QComboBox()
        for label, name in vfx_lab.ATTACH_SOCKET_WHITELIST:
            socket_combo.addItem(f"{label}   ({name})", userData=name)
        socket_combo.addItem("— Custom… —", userData="__custom__")
        rlay.addWidget(socket_combo)

        custom_edit = QLineEdit()
        custom_edit.setPlaceholderText("Custom socket name (exact bone name, case-sensitive)")
        custom_edit.setEnabled(False)
        rlay.addWidget(custom_edit)

        queue_list = QListWidget()
        rlay.addWidget(QLabel("Queued attach changes:"))
        rlay.addWidget(queue_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Queue Attach Change")
        add_btn.setStyleSheet("background: #F9A825; color: black; font-weight: bold;")
        rm_btn = QPushButton("Remove Selected")
        btn_row.addWidget(add_btn); btn_row.addWidget(rm_btn); btn_row.addStretch()
        rlay.addLayout(btn_row)
        split.addWidget(right, 3)

        def refresh_list():
            q = search.text().strip()
            cat = cat_combo.currentText()
            owned = owned_cb.isChecked()
            self._vfx_populate_list(
                lst, summaries,
                lambda s: s.has_dock and self._vfx_filter_match(s, q, cat, owned, owned_keys),
                lambda s: f"{s.display_name or s.internal_name}  [{s.category}]  ← {s.dock_parent_socket}")

        def refresh_queue():
            queue_list.clear()
            for ac in queue:
                s = by_key.get(ac.item_key)
                nm = (s.display_name or s.internal_name) if s else f"key={ac.item_key}"
                queue_list.addItem(f"{nm}  →  {ac.new_parent_socket}")

        def on_select():
            it = lst.currentItem()
            if not it:
                cur_lbl.setText("Select a dockable item on the left.")
                return
            s = by_key.get(it.data(Qt.UserRole))
            if not s:
                return
            cur_lbl.setText(
                f"{s.display_name or s.internal_name}\n"
                f"Current parent socket: {s.dock_parent_socket or '(none)'}\n"
                f"Child socket: {s.dock_child_socket or '(none)'}")

        def on_combo(_):
            is_custom = socket_combo.currentData() == "__custom__"
            custom_edit.setEnabled(is_custom)

        def on_add():
            it = lst.currentItem()
            if not it:
                return
            s = by_key.get(it.data(Qt.UserRole))
            if not s:
                return
            chosen = socket_combo.currentData()
            if chosen == "__custom__":
                name = custom_edit.text().strip()
                if not name:
                    QMessageBox.information(tab, "Attach", "Enter a custom socket name.")
                    return
            else:
                name = chosen
            queue[:] = [a for a in queue if a.item_key != s.item_key]
            queue.append(vfx_lab.AttachChange(item_key=s.item_key, new_parent_socket=name))
            refresh_queue()

        def on_remove():
            row = queue_list.currentRow()
            if 0 <= row < len(queue):
                del queue[row]
                refresh_queue()

        search.textChanged.connect(lambda _: refresh_list())
        cat_combo.currentTextChanged.connect(lambda _: refresh_list())
        owned_cb.stateChanged.connect(lambda _: refresh_list())
        lst.currentItemChanged.connect(lambda *_: on_select())
        socket_combo.currentIndexChanged.connect(on_combo)
        add_btn.clicked.connect(on_add)
        rm_btn.clicked.connect(on_remove)
        refresh_list(); refresh_queue()
        return tab


    def _buff_open_transmog_dialog(self) -> None:
        """Open the Transmog dialog — armor visual swapping."""
        if not self._armor_catalog:
            if self._buff_data is None:
                QMessageBox.information(self, "Transmog",
                    "Click 'Extract' first to load iteminfo data.")
                return
            try:
                from armor_catalog import parse_armor_items
                self._armor_catalog = parse_armor_items(bytes(self._buff_data))
            except Exception as e:
                QMessageBox.critical(self, "Transmog", f"Armor catalog build failed: {e}")
                return
            if not self._armor_catalog:
                QMessageBox.warning(self, "Transmog", "No armor items found in iteminfo.")
                return

        from PySide6.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
            QComboBox, QLineEdit, QListWidget, QListWidgetItem, QFileDialog,
            QSplitter,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Transmog / Visual Swap")
        dlg.resize(1000, 680)
        dl = QVBoxLayout(dlg)

        header = QLabel(
            "Make YOUR armor look like another armor.\n"
            "Pick a piece you own on the LEFT, then pick the look you want from the RIGHT.\n"
            "Your stats/buffs/enchants are kept — only the visual model and textures change."
        )
        header.setWordWrap(True)
        header.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 4px;")
        dl.addWidget(header)

        filt_row = QHBoxLayout()
        filt_row.addWidget(QLabel("Category (both lists):"))
        cat_combo = QComboBox()
        cat_combo.addItems([
            "All",
            "Chest", "Gloves", "Boots", "Helm", "Cloak", "Shoulder",
            "OneHand Sword", "TwoHand Sword", "Dual Sword",
            "Dual Daggers", "TwoHand Axe", "Dual Axe",
            "Hammer", "Spear", "Bow",
            "Shield", "Bracer", "Lantern", "Torch",
            "Necklace", "Earring", "Ring", "Belt", "Trinket",
            "Other",
        ])
        filt_row.addWidget(cat_combo)
        only_owned_cb = QCheckBox("Only show items I own (left list)")
        only_owned_cb.setChecked(True)
        only_owned_cb.setToolTip(
            "Left list: filter to equipment you currently own.\n"
            "The right list always shows all items — you can use any look you want.")
        filt_row.addWidget(only_owned_cb)
        filt_row.addStretch(1)
        dl.addLayout(filt_row)

        splitter = QSplitter(Qt.Horizontal)

        tgt_panel = QWidget()
        tgt_l = QVBoxLayout(tgt_panel)
        tgt_l.setContentsMargins(2, 2, 2, 2)
        tgt_l.addWidget(QLabel("YOUR EQUIPMENT — pick the piece to re-skin:"))
        tgt_search = QLineEdit()
        tgt_search.setPlaceholderText("Search your items...")
        tgt_l.addWidget(tgt_search)
        tgt_list = QListWidget()
        tgt_list.setIconSize(QSize(32, 32))
        tgt_l.addWidget(tgt_list, 1)
        splitter.addWidget(tgt_panel)

        src_panel = QWidget()
        src_l = QVBoxLayout(src_panel)
        src_l.setContentsMargins(2, 2, 2, 2)
        src_l.addWidget(QLabel("NEW LOOK — pick the item whose look you want:"))
        src_search = QLineEdit()
        src_search.setPlaceholderText("Search all items...")
        src_l.addWidget(src_search)
        src_list = QListWidget()
        src_list.setIconSize(QSize(32, 32))
        src_l.addWidget(src_list, 1)
        splitter.addWidget(src_panel)

        splitter.setSizes([500, 500])
        dl.addWidget(splitter, 1)

        action_row = QHBoxLayout()
        add_btn = QPushButton("Add Swap")
        add_btn.setObjectName("accentBtn")
        add_btn.setToolTip("Add the selected Target+Source pair to the swap queue")
        action_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove Selected")
        action_row.addWidget(remove_btn)
        clear_btn = QPushButton("Clear All")
        action_row.addWidget(clear_btn)
        action_row.addStretch(1)
        import_btn = QPushButton("Import Config")
        import_btn.setToolTip("Load queued swaps from a JSON file")
        action_row.addWidget(import_btn)
        export_btn = QPushButton("Export Config")
        export_btn.setToolTip("Save queued swaps to a JSON file for sharing")
        action_row.addWidget(export_btn)
        dl.addLayout(action_row)

        dl.addWidget(QLabel("Queued swaps (applied on Export as Mod / Apply to Game):"))
        queue_list = QListWidget()
        queue_list.setMaximumHeight(140)
        dl.addWidget(queue_list)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("Save & Close")
        ok_btn.setObjectName("accentBtn")
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(cancel_btn)
        dl.addLayout(btn_row)

        local_swaps = list(self._transmog_swaps)

        owned_keys: set = set()
        try:
            for it in getattr(self, '_items', []) or []:
                if hasattr(it, 'item_key'):
                    owned_keys.add(it.item_key)
        except Exception:
            pass
        owned_count_label = QLabel(f"({len(owned_keys)} owned items detected)")
        owned_count_label.setStyleSheet(f"color: {COLORS['text_dim']}; font-size: 10px;")
        filt_row.addWidget(owned_count_label)

        def refresh_queue():
            queue_list.clear()
            for sw in local_swaps:
                src = sw['src']; tgt = sw['tgt']
                queue_list.addItem(f"{tgt.display_name} ({tgt.category})  →  now looks like  →  "
                                   f"{src.display_name} ({src.category})")

        def matches(a, cat, q):
            if cat != "All" and a.category != cat:
                return False
            if q:
                ql = q.lower()
                if ql not in a.display_name.lower() and ql not in a.internal_name.lower():
                    return False
            return True

        def _add_row(lst, a):
            label = f"[{a.category[:8]}] {a.display_name}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, a.item_id)
            if self._icon_cache:
                px = self._icon_cache.get_pixmap(a.item_id)
                if px:
                    item.setIcon(QIcon(px))
                elif self._icon_cache.has_icon(a.item_id):
                    self._icon_cache.request_icon(a.item_id, lambda *_: None)
            lst.addItem(item)

        def populate_target():
            prev_key = tgt_list.currentItem().data(Qt.UserRole) if tgt_list.currentItem() else None
            cat = cat_combo.currentText()
            q = tgt_search.text().strip()
            only_owned = only_owned_cb.isChecked()
            tgt_list.clear()
            restored_row = -1
            for a in self._armor_catalog:
                if not matches(a, cat, q):
                    continue
                if only_owned and owned_keys and a.item_id not in owned_keys:
                    continue
                _add_row(tgt_list, a)
                if a.item_id == prev_key:
                    restored_row = tgt_list.count() - 1
            if restored_row >= 0:
                tgt_list.setCurrentRow(restored_row)

        INVISIBLE_SENTINEL_KEY = -9999
        from armor_catalog import ArmorItem as _ArmorItem
        invisible_template = _ArmorItem(
            item_id=INVISIBLE_SENTINEL_KEY,
            internal_name='__INVISIBLE_ZERO__',
            display_name='Invisible Model',
            category='Invisible',
            hashes=[],
        )

        invisible_named_items = [
            a for a in self._armor_catalog if a.item_id == 1000491
        ]

        def populate_source():
            prev_key = src_list.currentItem().data(Qt.UserRole) if src_list.currentItem() else None
            q = src_search.text().strip()
            cat = cat_combo.currentText()
            src_list.clear()
            restored_row = -1

            show_invis = (not q or 'invis' in q.lower() or 'empty' in q.lower() or 'none' in q.lower() or 'ghost' in q.lower())
            if show_invis:
                for inv in invisible_named_items:
                    lbl = f"★ Invisible (Ghost_TwohandSword) — universal invisible"
                    it = QListWidgetItem(lbl)
                    it.setData(Qt.UserRole, inv.item_id)
                    it.setForeground(QBrush(QColor("#FFD700")))
                    src_list.addItem(it)
                    if prev_key == inv.item_id:
                        restored_row = src_list.count() - 1

            pinned_ids = {inv.item_id for inv in invisible_named_items} if show_invis else set()
            for a in self._armor_catalog:
                if a.item_id in pinned_ids:
                    continue
                if not matches(a, cat, q):
                    continue
                _add_row(src_list, a)
                if a.item_id == prev_key:
                    restored_row = src_list.count() - 1
            if restored_row >= 0:
                src_list.setCurrentRow(restored_row)

        def populate_both():
            populate_target()
            populate_source()

        cat_combo.currentTextChanged.connect(lambda _: populate_both())
        tgt_search.textChanged.connect(lambda _: populate_target())
        src_search.textChanged.connect(lambda _: populate_source())
        only_owned_cb.stateChanged.connect(lambda _: populate_target())
        populate_both()
        refresh_queue()

        def on_add():
            ti = tgt_list.currentItem()
            si = src_list.currentItem()
            if not ti or not si:
                QMessageBox.information(dlg, "Transmog",
                    "Pick ONE item in each list:\n"
                    "  Left  = your armor (the piece you want to re-skin)\n"
                    "  Right = the look you want it to have")
                return
            tgt_key = ti.data(Qt.UserRole)
            src_key = si.data(Qt.UserRole)
            if tgt_key == src_key:
                QMessageBox.information(dlg, "Transmog",
                    "Your armor and the new look must be different items.")
                return
            tgt = next((a for a in self._armor_catalog if a.item_id == tgt_key), None)
            if src_key == INVISIBLE_SENTINEL_KEY:
                src = invisible_template
                if src is None:
                    QMessageBox.warning(dlg, "Transmog",
                        "No Invisible Model template available in this iteminfo.")
                    return
                if not tgt:
                    return
                local_swaps[:] = [s for s in local_swaps if s['tgt'].item_id != tgt_key]
                import copy
                fake_src = copy.copy(src)
                fake_src.display_name = "Invisible Model"
                fake_src.category = "Invisible"
                local_swaps.append({'src': fake_src, 'tgt': tgt})
                refresh_queue()
                return
            src = next((a for a in self._armor_catalog if a.item_id == src_key), None)
            if not tgt or not src:
                return
            local_swaps[:] = [s for s in local_swaps if s['tgt'].item_id != tgt_key]
            local_swaps.append({'src': src, 'tgt': tgt})
            refresh_queue()

        def on_remove():
            row = queue_list.currentRow()
            if 0 <= row < len(local_swaps):
                del local_swaps[row]
                refresh_queue()

        def on_clear():
            local_swaps.clear()
            refresh_queue()

        def on_export():
            if not local_swaps:
                QMessageBox.information(dlg, "Export", "No swaps queued.")
                return
            path, _ = QFileDialog.getSaveFileName(
                dlg, "Export Transmog Config", "transmog_config.json", "JSON (*.json)")
            if not path:
                return
            out = {
                'version': 1,
                'swaps': [
                    {
                        'target_key': s['tgt'].item_id,
                        'target_name': s['tgt'].internal_name,
                        'source_key': s['src'].item_id,
                        'source_name': s['src'].internal_name,
                    }
                    for s in local_swaps
                ],
            }
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(out, f, indent=2)
                QMessageBox.information(dlg, "Export", f"Wrote {len(local_swaps)} swap(s) to:\n{path}")
            except Exception as e:
                QMessageBox.critical(dlg, "Export Failed", str(e))

        def on_import():
            path, _ = QFileDialog.getOpenFileName(
                dlg, "Import Transmog Config", "", "JSON (*.json)")
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                by_key = {a.item_id: a for a in self._armor_catalog}
                by_name = {a.internal_name: a for a in self._armor_catalog}
                added = 0
                missed = 0

                if isinstance(cfg.get('swaps'), list):
                    for s in cfg['swaps']:
                        tgt = by_key.get(s.get('target_key'))
                        src = by_key.get(s.get('source_key'))
                        if (not tgt or not src) and s.get('target_name') and s.get('source_name'):
                            tgt = tgt or by_name.get(s['target_name'])
                            src = src or by_name.get(s['source_name'])
                        if not tgt or not src:
                            missed += 1
                            log.warning("Transmog import: could not resolve target_key=%s source_key=%s",
                                        s.get('target_key'), s.get('source_key'))
                            continue
                        log.info("Transmog import: queued tgt=%s (key %s) <- src=%s (key %s)",
                                 tgt.internal_name, tgt.item_id, src.internal_name, src.item_id)
                        local_swaps[:] = [x for x in local_swaps if x['tgt'].item_id != tgt.item_id]
                        local_swaps.append({'src': src, 'tgt': tgt})
                        added += 1

                elif isinstance(cfg.get('patches'), list):
                    seen_pairs = set()
                    for patch in cfg['patches']:
                        for change in patch.get('changes', []):
                            label = change.get('label', '')
                            if ' -> ' not in label:
                                continue
                            tgt_name, src_name = label.split(' -> ', 1)
                            tgt_name, src_name = tgt_name.strip(), src_name.strip()
                            pair_key = (tgt_name, src_name)
                            if pair_key in seen_pairs:
                                continue
                            seen_pairs.add(pair_key)
                            src = by_name.get(src_name)
                            tgt = by_name.get(tgt_name)
                            if not src or not tgt:
                                missed += 1
                                continue
                            local_swaps[:] = [x for x in local_swaps if x['tgt'].item_id != tgt.item_id]
                            local_swaps.append({'src': src, 'tgt': tgt})
                            added += 1
                else:
                    QMessageBox.warning(dlg, "Import",
                        "Unrecognized JSON format. Expected either our 'swaps' format "
                        "or HexeMarie's 'patches' format.")
                    return

                refresh_queue()
                QMessageBox.information(dlg, "Import",
                    f"Imported {added} swap(s). {missed} skipped (items not found).")
            except Exception as e:
                QMessageBox.critical(dlg, "Import Failed", str(e))

        add_btn.clicked.connect(on_add)
        remove_btn.clicked.connect(on_remove)
        clear_btn.clicked.connect(on_clear)
        export_btn.clicked.connect(on_export)
        import_btn.clicked.connect(on_import)

        def on_ok():
            self._transmog_swaps = list(local_swaps)
            self._buff_modified = self._buff_modified or bool(local_swaps)
            count = len(local_swaps)
            self._buff_status_label.setText(
                f"Transmog queue: {count} swap(s). Applied on Export as Mod / Apply to Game.")
            dlg.accept()

        ok_btn.clicked.connect(on_ok)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()


    def _buff_preview_item(self) -> None:
        """Show an in-game-style tooltip preview of the selected item with current mods."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Preview", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Preview", "Select an item first.")
            return

        item = self._buff_current_item
        rust_info = self._buff_rust_lookup.get(item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "Preview", "No Rust data for this item.")
            return

        display_name = self._name_db.get_name(item.item_key)
        if display_name.startswith("Unknown"):
            display_name = item.name

        equip_type = rust_info.get('equip_type_info', 0)
        category = rust_info.get('category_info', 0)
        tier = rust_info.get('item_tier', 0)
        tier_names = {0: "", 1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Legendary"}
        tier_colors = {0: "#AAAAAA", 1: "#AAAAAA", 2: "#4FC3F7", 3: "#81C784",
                       4: "#CE93D8", 5: "#FFB74D"}
        tier_name = tier_names.get(tier, "")
        tier_color = tier_colors.get(tier, "#AAAAAA")

        limits = self._buff_item_limits.get(str(item.item_key), {})
        slot_type = limits.get('slotType', -1)
        if slot_type == 65535 or slot_type == -1:
            type_str = "Item"
        elif slot_type <= 3:
            type_str = "Weapon"
        elif slot_type <= 9:
            type_str = "Armor"
        else:
            type_str = "Equipment"

        display_level = 0
        if hasattr(self, '_eb_level_target'):
            sel = self._eb_level_target.currentData()
            edl = rust_info.get('enchant_data_list', [])
            if sel is not None and sel >= 0 and sel < len(edl):
                display_level = sel

        _PREVIEW_STAT_NAMES = {
            1000000: "Max HP", 1000001: "Fatal", 1000002: "Attack",
            1000003: "Defense", 1000004: "Accuracy",
            1000005: "Base Attack", 1000006: "Critical Damage",
            1000007: "Critical Rate", 1000008: "Incoming Dmg Rate",
            1000009: "Incoming Dmg Reduction", 1000010: "Attack Speed",
            1000011: "Movement Speed", 1000012: "Climb Speed",
            1000013: "Swim Speed", 1000016: "Fire Resistance",
            1000017: "Ice Resistance", 1000018: "Lightning Resistance",
            1000024: "Fire Resist", 1000025: "Ice Resist",
            1000026: "Stamina Regen", 1000027: "MP Regen",
            1000031: "Hit Rate", 1000035: "Max Damage Rate",
            1000036: "Pressure", 1000037: "Stamina Cost Reduction",
            1000043: "Guard PV Rate", 1000046: "MP Cost Reduction",
            1000047: "Money Drop Rate", 1000049: "Equip Drop Rate",
            1000050: "DPV Rate",
        }

        html_parts = []
        html_parts.append(f"""
        <div style="
            background-color: #1a1a2e;
            border: 2px solid #3a3a5c;
            border-radius: 8px;
            padding: 16px;
            min-width: 320px;
            max-width: 420px;
            font-family: 'Segoe UI', Arial, sans-serif;
        ">
        """)

        html_parts.append(f"""
            <div style="font-size: 18px; font-weight: bold; color: {tier_color};
                        margin-bottom: 2px;">{display_name}</div>
            <div style="font-size: 12px; color: #8888aa; margin-bottom: 10px;">
                {tier_name + ' | ' if tier_name else ''}{type_str}</div>
        """)

        edl = rust_info.get('enchant_data_list', [])
        if edl and display_level < len(edl):
            ed = edl[display_level]
            sd = ed.get('enchant_stat_data', {})

            _RATE_PCT_HASHES = {
                1000008,
                1000009,
            }
            flat_stats = sd.get('stat_list_static', [])
            for s in flat_stats:
                sname = _PREVIEW_STAT_NAMES.get(s['stat'], f"Stat {s['stat']}")
                raw = s['change_mb']
                if s['stat'] in _RATE_PCT_HASHES:
                    pct = raw / 10_000_000
                    disp_str = f"{pct:.4f}".rstrip('0').rstrip('.') + '%'
                    icon = "\U0001f4ca"
                    color = "#CE93D8"
                else:
                    display_val = raw / 1000
                    disp_str = f"{display_val:,.0f}" if display_val == int(display_val) else f"{display_val:,.1f}"
                    color = "#ffffff"
                    if s['stat'] == 1000002:
                        icon = "\u2694\ufe0f"
                    elif s['stat'] == 1000003:
                        icon = "\U0001f6e1\ufe0f"
                    elif s['stat'] == 1000000:
                        icon = "\u2764\ufe0f"
                    else:
                        icon = "\u2b50"
                html_parts.append(f"""
                    <div style="font-size: 14px; color: #e0e0e0; padding: 2px 0;">
                        <span style="color: #FFB74D;">{icon}</span>
                        <span style="color: #e0e0e0;">{sname}</span>
                        <span style="float: right; color: {color}; font-weight: bold;">{disp_str}</span>
                    </div>
                """)

            rate_stats = sd.get('stat_list_static_level', [])
            for s in rate_stats:
                sname = _PREVIEW_STAT_NAMES.get(s['stat'], f"Stat {s['stat']}")
                val = s['change_mb']
                html_parts.append(f"""
                    <div style="font-size: 14px; color: #e0e0e0; padding: 2px 0;">
                        <span style="color: #81C784;">\u26a1</span>
                        <span style="color: #e0e0e0;">{sname}</span>
                        <span style="float: right; color: #81C784;">Lv {val}</span>
                    </div>
                """)

            regen_stats = sd.get('regen_stat_list', [])
            for s in regen_stats:
                sname = _PREVIEW_STAT_NAMES.get(s['stat'], f"Regen {s['stat']}")
                raw = s['change_mb']
                display_val = raw / 1000
                disp_str = f"{display_val:,.0f}" if display_val == int(display_val) else f"{display_val:,.1f}"
                html_parts.append(f"""
                    <div style="font-size: 14px; color: #e0e0e0; padding: 2px 0;">
                        <span style="color: #4FC3F7;">\u267b\ufe0f</span>
                        <span style="color: #e0e0e0;">{sname}</span>
                        <span style="float: right; color: #4FC3F7;">{disp_str}</span>
                    </div>
                """)

        sharp = rust_info.get('sharpness_data', {})
        max_sharp = sharp.get('max_sharpness', 0)
        if max_sharp > 0:
            bars = "\u2588" * max_sharp
            html_parts.append(f"""
                <div style="font-size: 14px; color: #e0e0e0; padding: 2px 0;">
                    <span style="color: #FFB74D;">\u2728</span>
                    <span style="color: #e0e0e0;">Refinement</span>
                    <span style="float: right; color: #FFB74D; letter-spacing: 1px;">{bars}</span>
                </div>
            """)

        html_parts.append('<hr style="border: 1px solid #3a3a5c; margin: 8px 0;">')

        psl = rust_info.get('equip_passive_skill_list', [])
        if psl:
            for ps in psl:
                sk_name = self._PASSIVE_SKILL_NAMES.get(ps['skill'], f"Skill {ps['skill']}")
                level_str = f"Lv {ps['level']}" if ps['level'] > 1 else ""
                html_parts.append(f"""
                    <div style="font-size: 13px; color: #66BB6A; padding: 2px 0;">
                        <span style="color: #66BB6A;">\u2618</span>
                        {sk_name} {level_str}
                    </div>
                """)
            html_parts.append('<hr style="border: 1px solid #3a3a5c; margin: 8px 0;">')

        if edl and display_level < len(edl):
            buffs = edl[display_level].get('equip_buffs', [])
            if buffs:
                for b in buffs:
                    bname = self._EQUIP_BUFF_NAMES.get(b['buff'], f"Buff {b['buff']}")
                    blvl = b['level']
                    html_parts.append(f"""
                        <div style="font-size: 13px; color: #FFD54F; padding: 2px 0;">
                            <span style="color: #FFD54F;">\u25c9</span>
                            {bname}
                            <span style="color: #FFD54F; float: right;">Lv {blvl}</span>
                        </div>
                    """)

        if edl and len(edl) > 1:
            html_parts.append(f"""
                <div style="font-size: 11px; color: #666688; margin-top: 8px; text-align: center;">
                    Showing enchant level +{display_level} of {len(edl) - 1}
                </div>
            """)

        html_parts.append("</div>")

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Item Preview: {display_name}")
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        dl = QVBoxLayout(dlg)
        dl.setContentsMargins(8, 8, 8, 8)

        content_row = QHBoxLayout()

        icon_label = QLabel()
        px = self._icon_cache.get_pixmap(item.item_key)
        if px and not px.isNull():
            icon_label.setPixmap(px.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            icon_label.setText("No icon")
            icon_label.setStyleSheet("color: #666; font-size: 12px;")
        icon_label.setFixedSize(100, 100)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet(
            "background-color: #0d0d1a; border: 2px solid #3a3a5c; border-radius: 8px;"
        )
        content_row.addWidget(icon_label)

        tooltip_label = QLabel()
        tooltip_label.setTextFormat(Qt.RichText)
        tooltip_label.setWordWrap(True)
        tooltip_label.setText("".join(html_parts))
        tooltip_label.setStyleSheet("background: transparent;")
        content_row.addWidget(tooltip_label, 1)

        dl.addLayout(content_row)

        if edl and len(edl) > 1:
            level_switch = QHBoxLayout()
            level_switch.addWidget(QLabel("Preview enchant level:"))
            level_combo = QComboBox()
            for i in range(len(edl)):
                level_combo.addItem(f"+{i}", i)
            level_combo.setCurrentIndex(display_level)

            def _switch_level(idx):
                nonlocal display_level
                display_level = idx
                dlg.close()
                old_idx = self._eb_level_target.currentIndex()
                self._eb_level_target.setCurrentIndex(idx + 1)
                self._buff_preview_item()
                self._eb_level_target.setCurrentIndex(old_idx)

            level_combo.currentIndexChanged.connect(_switch_level)
            level_switch.addWidget(level_combo)
            level_switch.addStretch()
            dl.addLayout(level_switch)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.close)
        dl.addWidget(close_btn)

        dlg.setStyleSheet(f"""
            QDialog {{
                background-color: #0d0d1a;
            }}
            QLabel {{
                color: #e0e0e0;
            }}
            QComboBox {{
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: 1px solid #3a3a5c;
                padding: 4px;
            }}
            QPushButton {{
                background-color: #2a2a4e;
                color: #e0e0e0;
                border: 1px solid #3a3a5c;
                padding: 6px 16px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3a3a6e;
            }}
        """)

        dlg.adjustSize()
        dlg.exec()


    def _buff_stats_context_menu(self, pos) -> None:
        """Right-click context menu on the stats table — remove passives/buffs/stats."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            return

        table = self._buff_stats_table
        item = table.itemAt(pos)
        if not item:
            return

        row = item.row()
        name_cell = table.item(row, 0)
        if not name_cell:
            return

        kind_data = name_cell.data(Qt.UserRole + 1)
        if not kind_data:
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(table)
        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        kind = kind_data[0]

        if kind == 'passive':
            skill_id = kind_data[1]
            name = self._PASSIVE_SKILL_NAMES.get(skill_id, f"Skill {skill_id}")
            if isinstance(name, dict):
                name = name.get('suffix', name.get('english_name', str(skill_id)))
            act_remove = menu.addAction(f"Remove passive: {name}")
            act_remove_all = menu.addAction("Remove ALL passives")

            action = menu.exec(table.viewport().mapToGlobal(pos))
            if action == act_remove:
                psl = rust_info.get('equip_passive_skill_list', []) or []
                rust_info['equip_passive_skill_list'] = [p for p in psl if p['skill'] != skill_id]
                self._buff_modified = True
                self._buff_refresh_stats()
                self._buff_status_label.setText(f"Removed passive {name}")
            elif action == act_remove_all:
                rust_info['equip_passive_skill_list'] = []
                self._buff_modified = True
                self._buff_refresh_stats()
                self._buff_status_label.setText("Removed all passives")

        elif kind == 'buff':
            buff_id = kind_data[1]
            name = self._EQUIP_BUFF_NAMES.get(buff_id, f"Buff {buff_id}")
            act_remove = menu.addAction(f"Remove buff: {name}")
            act_remove_all = menu.addAction("Remove ALL buffs")

            action = menu.exec(table.viewport().mapToGlobal(pos))
            edl = rust_info.get('enchant_data_list', [])
            if action == act_remove:
                removed_levels = 0
                for ed in edl:
                    old = ed.get('equip_buffs', []) or []
                    new = [b for b in old if b['buff'] != buff_id]
                    if len(new) < len(old):
                        ed['equip_buffs'] = new
                        removed_levels += 1
                self._buff_modified = True
                self._buff_refresh_stats()
                self._buff_status_label.setText(f"Removed buff {name} ({removed_levels} levels)")
            elif action == act_remove_all:
                for ed in edl:
                    ed['equip_buffs'] = []
                self._buff_modified = True
                self._buff_refresh_stats()
                self._buff_status_label.setText("Removed all buffs")

        elif kind == 'stat':
            stat_key = kind_data[1]
            list_name = kind_data[2]
            _STAT_NAMES = {
                1000000: "HP", 1000002: "DDD", 1000003: "DPV",
                1000007: "Crit Rate", 1000010: "Attack Speed",
                1000011: "Move Speed",
            }
            name = _STAT_NAMES.get(stat_key, f"Stat {stat_key}")
            act_remove = menu.addAction(f"Remove stat: {name}")

            action = menu.exec(table.viewport().mapToGlobal(pos))
            if action == act_remove:
                edl = rust_info.get('enchant_data_list', [])
                removed = 0
                for ed in edl:
                    sd = ed.get('enchant_stat_data', {})
                    existing = sd.get(list_name, [])
                    new = [s for s in existing if s['stat'] != stat_key]
                    if len(new) < len(existing):
                        sd[list_name] = new
                        removed += 1
                self._buff_modified = True
                self._buff_refresh_stats()
                self._buff_status_label.setText(f"Removed stat {name} ({removed} levels)")


    def _eb_remove_passive(self) -> None:
        """Remove a passive skill from the selected item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        psl = rust_info.get('equip_passive_skill_list', [])
        if not psl:
            QMessageBox.information(self, "Remove Passive", "This item has no passives.")
            return

        target_skill = self._eb_passive_combo.currentData()
        target_name = self._PASSIVE_SKILL_NAMES.get(target_skill, f"Skill {target_skill}")

        new_psl = [p for p in psl if p['skill'] != target_skill]
        if len(new_psl) == len(psl):
            QMessageBox.information(self, "Remove Passive",
                f"{target_name} is not on this item.")
            return

        rust_info['equip_passive_skill_list'] = new_psl
        self._buff_modified = True
        self._buff_refresh_stats()
        self._eb_status.setText(f"Removed {target_name} ({len(new_psl)} passives remain)")


    def _eb_god_mode(self) -> None:
        """Apply Potter's God Mode to the selected item — full stat injection via Rust dicts.

        This is the CORRECT way to add buffs — modifies structured data, not raw bytes.
        Adds: passive skills, regen stats, flat stats, level stats, and equipment buffs.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "God Mode", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "God Mode", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "God Mode", "Item not found in Rust data.")
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            QMessageBox.warning(self, "God Mode",
                "This item has no enchant data.\n"
                "Only equippable items (weapons, armor, accessories) can have buffs.")
            return

        display_name = self._name_db.get_name(self._buff_current_item.item_key)

        reply = QMessageBox.warning(
            self, "Potter's God Mode",
            f"Apply God Mode to {display_name}?\n\n"
            f"This will inject into ALL {len(edl)} enchant levels:\n"
            f"  - Passive: Invincible + Great Thief\n"
            f"  - Regen: Stamina 100K, MP 100K\n"
            f"  - Static: DDD 999999, DPV 999999, Stamina Reduction 100M\n"
            f"  - Levels: AtkSpd 10, MoveSpd 10, CritRate 10, Resistances 10\n"
            f"  - Buffs: 8 equipment buffs at level 10\n\n"
            f"Click 'Export as Mod' after to write.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        rust_info['equip_passive_skill_list'] = [
            {"skill": 70994, "level": 1},
            {"skill": 9128, "level": 1},
        ]

        for ed in edl:
            sd = ed.setdefault('enchant_stat_data', {})

            sd['regen_stat_list'] = [
                {"stat": 1000026, "change_mb": 100000},
                {"stat": 1000027, "change_mb": 100000},
            ]

            sd['stat_list_static'] = [
                {"stat": 1000002, "change_mb": 999999},
                {"stat": 1000003, "change_mb": 999999},
                {"stat": 1000037, "change_mb": 100000000},
            ]

            sd['stat_list_static_level'] = [
                {"stat": 1000010, "change_mb": 10},
                {"stat": 1000011, "change_mb": 10},
                {"stat": 1000007, "change_mb": 10},
                {"stat": 1000024, "change_mb": 10},
                {"stat": 1000025, "change_mb": 10},
                {"stat": 1000026, "change_mb": 10},
            ]

            ed['equip_buffs'] = [
                {"buff": 1000072, "level": 10},
                {"buff": 1000071, "level": 10},
                {"buff": 1000073, "level": 10},
                {"buff": 1000093, "level": 10},
                {"buff": 1000091, "level": 10},
                {"buff": 1000124, "level": 10},
                {"buff": 1000123, "level": 10},
                {"buff": 1000114, "level": 10},
            ]

        self._buff_modified = True
        self._buff_refresh_stats()
        self._buff_status_label.setText(
            f"God Mode applied to {display_name} — "
            f"passives + stats + buffs injected into {len(edl)} enchant levels. "
            f"Click 'Export as Mod' to write."
        )


    def _build_effect_catalog(self, items):
        """Build the effect catalog combo from all items with gimmick effects.

        Uses gimmickinfo_parser to resolve gimmick key -> name for better labels.
        Stores full (label, data) list in _effect_catalog_all for search filtering.
        """
        self._effect_catalog_combo.clear()
        self._effect_catalog_data = {}
        self._effect_catalog_all = []

        gimmick_names = {}
        try:
            import crimson_rs
            game_path = self._config.get("game_install_path", "")
            if game_path:
                dp = 'gamedata/binary__/client/bin'
                gi_body = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'gimmickinfo.pabgb'))
                gi_gh = bytes(crimson_rs.extract_file(game_path, '0008', dp, 'gimmickinfo.pabgh'))
                from gimmickinfo_parser import parse_all_gimmicks
                full, partial, _ = parse_all_gimmicks(gi_body, gi_gh)
                for e in full + partial:
                    k = e.get('key', 0)
                    n = e.get('name', '')
                    if k and n:
                        display = n.replace('gimmick_equip_', '').replace('gimmick_', '')
                        gimmick_names[k] = display
                log.info("Loaded %d gimmick names for effect catalog", len(gimmick_names))
        except Exception as ge:
            log.warning("Could not load gimmick names: %s", ge)

        seen_gimmicks = {}
        for item in items:
            psl = item.get('equip_passive_skill_list', [])
            gi = item.get('gimmick_info', 0)
            if not psl or not gi:
                continue
            if gi >= 18000000:
                continue

            if gi in seen_gimmicks:
                continue
            seen_gimmicks[gi] = True

            skill_parts = []
            for s in psl:
                sid = s['skill']
                sname = self._PASSIVE_SKILL_NAMES.get(sid, {})
                if isinstance(sname, dict):
                    display = sname.get('suffix', sname.get('english_name', str(sid)))
                else:
                    display = str(sname) if sname else str(sid)
                skill_parts.append(display)

            src = item.get('string_key', '')
            gi_name = gimmick_names.get(gi, '')
            if gi_name:
                label = f"{gi_name}  ({' + '.join(skill_parts)})  [{src}]"
            else:
                label = f"{' + '.join(skill_parts)}  [{src}]  gi={gi}"

            dcd = item.get('docking_child_data')

            effect_data = {
                'equip_passive_skill_list': psl,
                'gimmick_info': gi,
                'cooltime': max(item.get('cooltime', 0), 1),
                'item_charge_type': item.get('item_charge_type', 0),
                'max_charged_useable_count': max(item.get('max_charged_useable_count', 0), 1),
                'respawn_time_seconds': item.get('respawn_time_seconds', 0),
                'docking_child_data': dcd,
                'source_key': item.get('key'),
                'source_name': src,
                'gimmick_name': gi_name,
            }

            idx = len(self._effect_catalog_data)
            self._effect_catalog_data[idx] = effect_data
            self._effect_catalog_all.append((label, idx))

        self._effect_catalog_all.sort(key=lambda x: x[0].lower())

        self._effect_populate_combo("")


    def _effect_populate_combo(self, filter_text: str) -> None:
        """Populate the effect combo, filtered by search text."""
        if not hasattr(self, '_effect_catalog_all'):
            return
        self._effect_catalog_combo.blockSignals(True)
        self._effect_catalog_combo.clear()

        ft = filter_text.strip().lower()
        shown = 0
        for label, idx in self._effect_catalog_all:
            data = self._effect_catalog_data.get(idx, {})
            haystack = label.lower()
            haystack += ' ' + str(data.get('source_name', '')).lower()
            haystack += ' ' + str(data.get('gimmick_info', ''))
            haystack += ' ' + str(data.get('gimmick_name', '')).lower()
            for s in data.get('equip_passive_skill_list', []):
                haystack += ' ' + str(s.get('skill', ''))
            if not ft or ft in haystack:
                self._effect_catalog_combo.addItem(label, idx)
                shown += 1

        header = f"-- {shown} effect(s) --" if ft else f"-- {len(self._effect_catalog_all)} effects available --"
        self._effect_catalog_combo.insertItem(0, header, None)
        self._effect_catalog_combo.setCurrentIndex(0)
        self._effect_catalog_combo.blockSignals(False)


    def _effect_filter_changed(self, text: str) -> None:
        """Handle search box text change."""
        self._effect_populate_combo(text)


    def _eb_copy_effect(self):
        """Copy a gimmick effect from the catalog onto the selected item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Copy Effect", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Copy Effect", "Select an item first.")
            return

        idx = self._effect_catalog_combo.currentData()
        if idx is None:
            QMessageBox.warning(self, "Copy Effect", "Select an effect from the dropdown.")
            return

        effect = self._effect_catalog_data.get(idx)
        if not effect:
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "Copy Effect", "Item not found in Rust data.")
            return

        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        src_name = effect.get('source_name', '?')
        skills = effect.get('equip_passive_skill_list', [])
        skill_str = ', '.join(str(s['skill']) for s in skills)

        cur_passives = rust_info.get('equip_passive_skill_list', []) or []
        cur_skill_str = ', '.join(str(p['skill']) for p in cur_passives) or '(none)'

        reply = QMessageBox.question(
            self, "Apply Effect",
            f"Apply effect from {src_name} to {display_name}?\n\n"
            f"Current passives: {cur_skill_str}\n"
            f"Adding skills: {skill_str}\n"
            f"Gimmick: {effect.get('gimmick_info', 0)}\n"
            f"Cooltime: {effect.get('cooltime', 0)}s\n"
            f"Charges: {effect.get('max_charged_useable_count', 0)}\n"
            f"Has docking: {'Yes' if effect.get('docking_child_data') else 'No'}\n\n"
            f"Passives will STACK (existing + new, deduped by skill ID).\n"
            f"Gimmick/docking/cooltime will REPLACE (one gimmick slot per item).\n\n"
            f"Click 'Export as Mod' after to write.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        existing_passives = list(rust_info.get('equip_passive_skill_list', []) or [])
        existing_keys = {p['skill'] for p in existing_passives}
        merged = existing_passives[:]
        added_count = 0
        for p in effect.get('equip_passive_skill_list', []):
            if p['skill'] not in existing_keys:
                merged.append(p)
                existing_keys.add(p['skill'])
                added_count += 1
        rust_info['equip_passive_skill_list'] = merged

        for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                    'max_charged_useable_count', 'docking_child_data',
                    'respawn_time_seconds'):
            if gf in effect:
                rust_info[gf] = effect[gf]

        self._buff_modified = True
        self._buff_refresh_stats()
        self._eb_status.setText(
            f"Effect from {src_name} applied: +{added_count} new passive(s), "
            f"{len(merged)} total. Gimmick replaced."
        )

    _ITEM_PRESETS = {
        "shadow_boots": {
            "name": "Shadow Boots",
            "passives": [
                {"skill": 7201, "level": 1},
                {"skill": 7055, "level": 1},
                {"skill": 7202, "level": 1},
            ],
            "gimmick_info": 1004431,
            "cooltime": 1,
            "item_charge_type": 0,
            "max_charged_useable_count": 100,
            "respawn_time_seconds": 0,
            "docking_child_data": {
                "gimmick_info_key": 1004431,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Bip01 Footsteps",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [247236102, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 0,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            },
        },
        "lightning_weapon": {
            "name": "Lightning Weapon",
            "passives": [
                {"skill": 91101, "level": 3},
                {"skill": 91104, "level": 3},
                {"skill": 91105, "level": 3},
            ],
            "gimmick_info": 1001961,
            "cooltime": 1,
            "item_charge_type": 0,
            "max_charged_useable_count": 100,
            "respawn_time_seconds": 0,
            "docking_child_data": {
                "gimmick_info_key": 1001961,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [3365725887, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 1,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            },
        },
        "great_thief": {
            "name": "Great Thief (Block Theft only)",
            "passives": [
                {"skill": 9128, "level": 1},
                {"skill": 76009, "level": 1},
            ],
            "gimmick_info": 1002041,
            "cooltime": 1800,
            "item_charge_type": 0,
            "max_charged_useable_count": 1,
            "respawn_time_seconds": 0,
            "docking_child_data": {
                "gimmick_info_key": 1002041,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Gimmick_Hand_L_00_Socket",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [0, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 0,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            },
        },
        "great_thief_all": {
            "name": "Great Thief (Block ALL crime)",
            "passives": [
                {"skill": 9128, "level": 1},
                {"skill": 76009, "level": 1},
                {"skill": 76011, "level": 1},
                {"skill": 76012, "level": 1},
            ],
            "gimmick_info": 1002041,
            "cooltime": 1800,
            "item_charge_type": 0,
            "max_charged_useable_count": 1,
            "respawn_time_seconds": 0,
            "docking_child_data": {
                "gimmick_info_key": 1002041,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Gimmick_Hand_L_00_Socket",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [0, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 0,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            },
        },
        "crime_mask": {
            "name": "Crime Mask (Steal / Threaten)",
            "passives": [
                {"skill": 709, "level": 1},
            ],
        },
    }


    def _eb_great_thief_pick_variant(self) -> None:
        """Show a picker so the user can choose which Great Thief variant to apply."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

        dlg = QDialog(self)
        dlg.setWindowTitle("Great Thief — Pick Variant")
        dlg.resize(480, 220)
        dl = QVBoxLayout(dlg)

        info = QLabel(
            "Pick which variant of Great Thief to apply.\n\n"
            "Block Theft only: skills 9128 + 76009. Suppresses pickpocket crime detection.\n"
            "Other crimes (vandalism, assault) will still flag you.\n\n"
            "Block ALL crime: also adds 76011 + 76012. Full crime immunity —\n"
            "theft, vandalism, and all other crime types.\n\n"
            "Passives stack with existing; gimmick replaces."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {COLORS['text_dim']}; padding: 4px;")
        dl.addWidget(info)

        btn_row = QHBoxLayout()
        b1 = QPushButton("Block Theft only")
        b1.clicked.connect(lambda: (dlg.accept(), self._eb_apply_preset("great_thief")))
        btn_row.addWidget(b1)

        b2 = QPushButton("Block ALL crime")
        b2.setObjectName("accentBtn")
        b2.clicked.connect(lambda: (dlg.accept(), self._eb_apply_preset("great_thief_all")))
        btn_row.addWidget(b2)

        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel)
        dl.addLayout(btn_row)

        dlg.exec()


    def _eb_apply_preset(self, preset_key: str) -> None:
        """Apply a known-working preset (Potter's configs) to the current item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Apply Preset", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Apply Preset", "Select an item first.")
            return

        preset = self._ITEM_PRESETS.get(preset_key)
        if not preset:
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        skill_str = ', '.join(str(p['skill']) for p in preset['passives'])

        cur_charge = rust_info.get('item_charge_type', 0)
        new_charge = preset.get('item_charge_type', cur_charge)
        charge_change_warn = ""
        if cur_charge != new_charge and new_charge == 0:
            charge_change_warn = (
                f"\n\nWARNING: Switching item from passive -> activated.\n"
                f"Existing copies in your save have NO charge-tracking data and\n"
                f"will show '0 uses' in-game. Get a FRESH copy (store/craft/drop)\n"
                f"AFTER applying the mod for the activation to work."
            )

        reply = QMessageBox.question(
            self, f"Apply Preset: {preset['name']}",
            f"Apply {preset['name']} preset to {display_name}?\n\n"
            f"Skills (stack with existing): {skill_str}\n"
            f"Gimmick: {preset.get('gimmick_info', 'unchanged')}\n"
            f"Replaces existing gimmick.{charge_change_warn}\n\n"
            f"Click 'Export as Mod' after to write.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        existing = list(rust_info.get('equip_passive_skill_list', []) or [])
        existing_keys = {p['skill'] for p in existing}
        added = 0
        for p in preset['passives']:
            if p['skill'] not in existing_keys:
                existing.append({'skill': p['skill'], 'level': p['level']})
                existing_keys.add(p['skill'])
                added += 1
        rust_info['equip_passive_skill_list'] = existing

        for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                    'max_charged_useable_count', 'respawn_time_seconds',
                    'docking_child_data'):
            if preset.get(gf) is not None:
                rust_info[gf] = preset[gf]

        self._buff_modified = True
        self._buff_refresh_stats()
        self._eb_status.setText(
            f"{preset['name']} applied to {display_name}: +{added} passive(s), "
            f"gimmick {preset['gimmick_info']}. Export as Mod to write."
        )


    def _load_vfx_catalog_into_combo(self) -> None:
        """Populate _eb_vfx_combo from vfx_equip_attachments.json (93 entries).

        Labelled as 'Gimmick' in the UI — entries are equip-gimmicks that
        attach to items. File is generated by _dump_vfx_gimmicks.py and
        bundled as a data asset. Each entry: {gimmick_key, gimmick_name,
        prefab_path, item_count}.
        """
        self._eb_vfx_combo.clear()
        self._eb_vfx_combo.addItem("(no gimmick selected)", None)
        self._vfx_catalog_entries: list = []
        for base in [os.path.dirname(os.path.abspath(__file__)),
                     getattr(sys, '_MEIPASS', ''), os.getcwd()]:
            path = os.path.join(base, 'vfx_equip_attachments.json')
            if os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._vfx_catalog_entries = data.get('gimmicks', []) or []
                    break
                except Exception:
                    continue
        for e in sorted(self._vfx_catalog_entries,
                        key=lambda x: (x.get('gimmick_name') or '').lower()):
            gk = e.get('gimmick_key')
            nm = e.get('gimmick_name') or f"gimmick {gk}"
            n_items = e.get('item_count', 0)
            pp = e.get('prefab_path') or ''
            leaf = pp.rsplit('/', 1)[-1].replace('.prefab', '') if pp else ''
            label = f"{nm}  ({gk}, {n_items} item)" + (f"  — {leaf}" if leaf else "")
            self._eb_vfx_combo.addItem(label, gk)


    def _eb_apply_vfx_gimmick(self) -> None:
        """Apply the Gimmick Browser's selected gimmick to the current item.

        Finds the first item in rust_items that uses this gimmick_info and
        clones its gimmick_info + docking_child_data + cooltime +
        item_charge_type + max_charged_useable_count + respawn_time_seconds
        onto the current item. Same mutation class as _eb_apply_preset but
        the source config comes from a live game item, not a hardcoded dict.

        Confirmed working: lantern gimmick on chest plate adds a usable
        lantern charge to the chest slot.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Apply Gimmick", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Apply Gimmick", "Select an item first.")
            return
        gk = self._eb_vfx_combo.currentData()
        if not gk:
            QMessageBox.information(self, "Apply Gimmick",
                                    "Pick a gimmick from the dropdown first.")
            return
        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return
        sample = None
        for it in self._buff_rust_items:
            if it.get('gimmick_info') == gk:
                sample = it
                break
        if sample is None:
            QMessageBox.warning(
                self, "Apply Gimmick",
                f"No sample item uses gimmick {gk}. Cannot clone config —\n"
                f"this gimmick may be referenced only indirectly.")
            return
        display = self._name_db.get_name(self._buff_current_item.item_key)
        entry = next((e for e in getattr(self, '_vfx_catalog_entries', [])
                      if e.get('gimmick_key') == gk), None)
        nm = (entry or {}).get('gimmick_name') or f"gimmick {gk}"
        cur_charge = rust_info.get('item_charge_type', 0)
        new_charge = sample.get('item_charge_type', cur_charge)
        warn = ""
        if cur_charge != new_charge and new_charge == 0:
            warn = ("\n\nNOTE: switching item to activated use. Existing save\n"
                    "copies have no charge-tracking data; get a FRESH drop.")
        reply = QMessageBox.question(
            self, "Apply Gimmick",
            f"Attach gimmick '{nm}' ({gk}) to {display}?\n\n"
            f"Cloning from sample item {sample.get('key')} "
            f"({sample.get('string_key', '?')}).{warn}\n\n"
            f"Click 'Export as Mod' after to write.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        import copy as _copy
        for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                   'max_charged_useable_count', 'respawn_time_seconds',
                   'docking_child_data'):
            if sample.get(gf) is not None:
                rust_info[gf] = _copy.deepcopy(sample[gf])
        self._buff_modified = True
        self._buff_refresh_stats()
        self._eb_status.setText(
            f"Gimmick '{nm}' applied to {display} ({gk}, cloned from item "
            f"{sample.get('key')}). Export as Mod to write."
        )


    def _eb_extend_sockets(self) -> None:
        """Extend socket capacity on the selected item.

        Adds entries to drop_default_data.add_socket_material_item_list
        matching the target count. Potter's discovery: list length = max sockets.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Extend Sockets", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Extend Sockets", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        ddd = rust_info.get('drop_default_data')
        if not ddd:
            QMessageBox.warning(self, "Extend Sockets",
                "This item has no drop_default_data — sockets not applicable.")
            return
        if not ddd.get('use_socket', 0):
            reply = QMessageBox.question(
                self, "Extend Sockets",
                "This item has use_socket=0 (sockets disabled).\n"
                "Enable socket support and extend anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
            ddd['use_socket'] = 1

        target_count = self._eb_socket_count.value()
        target_valid = self._eb_socket_valid.value()
        if target_valid > target_count:
            target_valid = target_count

        cur_list = ddd.get('add_socket_material_item_list', [])
        DEFAULT_COSTS = [500, 1000, 2000, 3000, 4000, 5000, 6000, 7000]

        new_list = list(cur_list)
        while len(new_list) < target_count:
            cost = DEFAULT_COSTS[len(new_list)] if len(new_list) < len(DEFAULT_COSTS) else 5000
            new_list.append({'item': 1, 'value': cost})
        new_list = new_list[:target_count]

        ddd['add_socket_material_item_list'] = new_list
        ddd['socket_valid_count'] = target_valid

        self._buff_modified = True
        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        self._eb_status.setText(
            f"Sockets on {display_name}: {target_count} max, {target_valid} pre-unlocked. "
            f"Export as Mod to write.")


    def _buff_on_buff_selected(self, index: int) -> None:
        """Update level spin range and hint when buff selection changes."""
        buff_key = self._eb_buff_combo.currentData()
        if buff_key and buff_key in self._buff_community_ranges:
            mn, mx, vtype = self._buff_community_ranges[buff_key]
            self._eb_buff_level.setRange(mn, mx)
            if self._eb_buff_level.value() > mx:
                self._eb_buff_level.setValue(mx)
            if self._eb_buff_level.value() < mn:
                self._eb_buff_level.setValue(mn)
            type_label = vtype if vtype else "?"
            self._buff_range_label.setText(f"[{mn}-{mx}] {type_label}")
            self._eb_buff_level.setToolTip(
                f"Value range: {mn} - {mx} ({type_label})\n"
                f"Community-verified range from buff_names_community.json")
        else:
            self._eb_buff_level.setRange(0, 100)
            self._buff_range_label.setText("[0-100] unverified")
            self._eb_buff_level.setToolTip(
                "Buff level (0-100, range unknown)\n"
                "Help us: verify in-game and contribute to buff_names_community.json")


    def _eb_add_buff(self) -> None:
        """Add an equipment buff to ALL enchant levels of the selected item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Add Buff", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Add Buff", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "Add Buff", "Item not found in Rust data.")
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            QMessageBox.warning(self, "Add Buff",
                "This item has no enchant data.\n"
                "Only equippable items (weapons, armor, accessories) can have buffs.")
            return

        buff_key = self._eb_buff_combo.currentData()
        buff_level = self._eb_buff_level.value()
        buff_name = self._EQUIP_BUFF_NAMES.get(buff_key, f"Buff {buff_key}")

        added = 0
        target_level = self._eb_level_target.currentData()

        for idx, ed in enumerate(edl):
            if target_level != -1 and idx != target_level:
                continue
            existing = ed.get('equip_buffs', [])
            already = any(b['buff'] == buff_key for b in existing)
            if already:
                for b in existing:
                    if b['buff'] == buff_key:
                        b['level'] = buff_level
                added += 1
            else:
                existing.append({'buff': buff_key, 'level': buff_level})
                ed['equip_buffs'] = existing
                added += 1

        self._buff_modified = True
        self._buff_refresh_stats()
        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        level_str = f"level +{target_level}" if target_level >= 0 else f"{added} enchant levels"
        self._buff_status_label.setText(
            f"Added {buff_name} Lv{buff_level} to {display_name} ({level_str}). "
            f"Click 'Export as Mod' to write."
        )


    def _eb_remove_buff(self) -> None:
        """Remove an equipment buff from ALL enchant levels of the selected item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Remove Buff", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Remove Buff", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "Remove Buff", "Item not found in Rust data.")
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            return

        buff_key = self._eb_buff_combo.currentData()
        buff_name = self._EQUIP_BUFF_NAMES.get(buff_key, f"Buff {buff_key}")

        removed = 0
        for ed in edl:
            existing = ed.get('equip_buffs', [])
            new_list = [b for b in existing if b['buff'] != buff_key]
            if len(new_list) < len(existing):
                removed += 1
            ed['equip_buffs'] = new_list

        if removed == 0:
            QMessageBox.information(self, "Remove Buff",
                f"{buff_name} not found on this item.")
            return

        self._buff_modified = True
        self._buff_refresh_stats()
        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        self._buff_status_label.setText(
            f"Removed {buff_name} from {display_name} ({removed} enchant levels). "
            f"Click 'Export as Mod' to write."
        )

    _DEV_PRESETS = {
        "immune": {
            "label": "Immune Ring",
            "passives": [{"skill": 70994, "level": 1}],
            "regen_stat_list": [{"stat": 1000000, "change_mb": 1000000}],
            "stat_list_static": [{"stat": 1000002, "change_mb": 1000000}],
        },
        "str_hp": {
            "label": "Str+HP Ring",
            "passives": [],
            "regen_stat_list": [{"stat": 1000000, "change_mb": 1000000}],
            "stat_list_static": [{"stat": 1000002, "change_mb": 1000000}],
        },
        "def_hp": {
            "label": "Def+HP Ring",
            "passives": [],
            "regen_stat_list": [{"stat": 1000000, "change_mb": 1000000}],
            "stat_list_static": [{"stat": 1000003, "change_mb": 1000000}],
        },
        "mp_stam": {
            "label": "MP+Stamina Ring",
            "passives": [],
            "regen_stat_list": [
                {"stat": 1000026, "change_mb": 100000},
                {"stat": 1000027, "change_mb": 100000},
            ],
            "stat_list_static": [
                {"stat": 1000037, "change_mb": 100000000},
            ],
        },
        "speed": {
            "label": "Speed Ring",
            "passives": [],
            "regen_stat_list": [],
            "stat_list_static": [],
            "stat_list_static_level": [
                {"stat": 1000010, "change_mb": 15},
                {"stat": 1000011, "change_mb": 15},
                {"stat": 1000007, "change_mb": 15},
            ],
        },
        "all": {
            "label": "All Dev Rings",
            "passives": [{"skill": 70994, "level": 1}],
            "regen_stat_list": [
                {"stat": 1000000, "change_mb": 1000000},
                {"stat": 1000026, "change_mb": 100000},
                {"stat": 1000027, "change_mb": 100000},
            ],
            "stat_list_static": [
                {"stat": 1000002, "change_mb": 1000000},
                {"stat": 1000003, "change_mb": 1000000},
                {"stat": 1000037, "change_mb": 100000000},
            ],
            "stat_list_static_level": [
                {"stat": 1000010, "change_mb": 15},
                {"stat": 1000011, "change_mb": 15},
                {"stat": 1000007, "change_mb": 15},
            ],
        },
        "elemental_weapon": {
            "label": "Elemental Weapon (Lightning+Ice+Fire)",
            "passives": [
                {"skill": 91101, "level": 3},
                {"skill": 91104, "level": 3},
                {"skill": 91105, "level": 3},
            ],
            "gimmick_info": 1001961,
            "cooltime": 1,
            "item_charge_type": 0,
            "max_charged_useable_count": 100,
            "respawn_time_seconds": 0,
            "docking_child_data": {
                "gimmick_info_key": 1001961,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Gimmick_Weapon_00_Socket",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [3365725887, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 1,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            },
        },
        "jump_boots": {
            "label": "Jump Boots (Dash+Breeze+Swimming)",
            "passives": [
                {"skill": 7201, "level": 1},
                {"skill": 7055, "level": 1},
                {"skill": 7202, "level": 1},
            ],
            "gimmick_info": 1004431,
            "cooltime": 1,
            "item_charge_type": 0,
            "max_charged_useable_count": 100,
            "respawn_time_seconds": 0,
            "docking_child_data": {
                "gimmick_info_key": 1004431,
                "character_key": 0,
                "item_key": 0,
                "attach_parent_socket_name": "Bip01 Footsteps",
                "attach_child_socket_name": "",
                "docking_tag_name_hash": [247236102, 0, 0, 0],
                "docking_equip_slot_no": 65535,
                "spawn_distance_level": 4294967295,
                "is_item_equip_docking_gimmick": 0,
                "send_damage_to_parent": 0,
                "is_body_part": 0,
                "docking_type": 0,
                "is_summoner_team": 0,
                "is_player_only": 0,
                "is_npc_only": 0,
                "is_sync_break_parent": 0,
                "hit_part": 0,
                "detected_by_npc": 0,
                "is_bag_docking": 0,
                "enable_collision": 0,
                "disable_collision_with_other_gimmick": 1,
                "docking_slot_key": "",
            },
        },
    }


    def _eb_apply_dev_preset(self) -> None:
        """Apply a dev ring preset to the selected item."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Dev Preset", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            QMessageBox.warning(self, "Dev Preset", "Select an item first.")
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            QMessageBox.warning(self, "Dev Preset", "Item not found in Rust data.")
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            QMessageBox.warning(self, "Dev Preset",
                "This item has no enchant data.\n"
                "Only equippable items can receive dev presets.")
            return

        preset_key = self._dev_preset_combo.currentData()
        preset = self._DEV_PRESETS.get(preset_key)
        if not preset:
            return

        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        reply = QMessageBox.question(
            self, f"Apply {preset['label']}",
            f"Apply {preset['label']} to {display_name}?\n\n"
            f"This injects into ALL {len(edl)} enchant levels:\n"
            f"  Passives: {len(preset.get('passives', []))}\n"
            f"  Regen stats: {len(preset.get('regen_stat_list', []))}\n"
            f"  Flat stats: {len(preset.get('stat_list_static', []))}\n"
            f"  Level stats: {len(preset.get('stat_list_static_level', []))}\n\n"
            f"Click 'Export as Mod' after to write.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        if preset.get('passives'):
            existing = rust_info.get('equip_passive_skill_list', [])
            for p in preset['passives']:
                if not any(e['skill'] == p['skill'] for e in existing):
                    existing.append(p)
            rust_info['equip_passive_skill_list'] = existing

        for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                    'max_charged_useable_count', 'docking_child_data'):
            if gf in preset:
                rust_info[gf] = preset[gf]

        for ed in edl:
            sd = ed.setdefault('enchant_stat_data', {})

            for field in ['regen_stat_list', 'stat_list_static', 'stat_list_static_level']:
                new_stats = preset.get(field, [])
                if not new_stats:
                    continue
                existing = sd.get(field, [])
                for ns in new_stats:
                    replaced = False
                    for i, es in enumerate(existing):
                        if es['stat'] == ns['stat']:
                            existing[i] = ns
                            replaced = True
                            break
                    if not replaced:
                        existing.append(ns)
                sd[field] = existing

        self._buff_modified = True
        self._buff_refresh_stats()
        self._buff_status_label.setText(
            f"Applied {preset['label']} to {display_name} ({len(edl)} levels). "
            f"Click 'Export as Mod' to write."
        )


    def _buff_export_mod(self) -> None:
        """Export modified iteminfo as a CDUMM-compatible mod folder.

        Uses crimson_rs.serialize_iteminfo() + pack_mod() to handle
        structural changes (added buffs, stats, passives, size changes).
        Output: folder with 0036/ + meta/ + modinfo.json.
        """
        if not self._buff_ensure_patcher():
            return

        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Export Mod",
                "Extract with Rust parser first (click 'Extract (Rust)').")
            return

        has_cd = bool(getattr(self, '_cd_patches', {}))
        if not self._buff_modified and not has_cd:
            apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
            if not apply_stacks:
                QMessageBox.information(self, "No Changes",
                    "No modifications have been made.\n"
                    "Add buffs, apply God Mode, check 'Max Stacks', or use 'No Cooldown (All Items)' first.")
                return

        apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
        if apply_stacks:
            target_val = self._stack_spin.value()
            for it in self._buff_rust_items:
                if it.get('max_stack_count', 1) > 1:
                    it['max_stack_count'] = target_val

        _mod_grp = f"{self._buff_modgroup_spin.value():04d}"
        reply = QMessageBox.question(
            self, "Export as Mod — Full PAZ Pack",
            f"This exports a full PAZ mod folder (like community mods).\n\n"
            f"WHAT THIS SUPPORTS:\n"
            f"  - Everything 'Export JSON Patch' can do, PLUS:\n"
            f"  - Add NEW equipment buffs (Fire Res, Ice Res, etc)\n"
            f"  - Add NEW stats that don't exist on the item\n"
            f"  - Add passive skills (Invincible, Great Thief, etc)\n"
            f"  - God Mode injection\n"
            f"  - Any edit that changes the file size\n\n"
            f"OUTPUT:\n"
            f"  A mod folder with {_mod_grp}/, meta/, and modinfo.json.\n"
            f"  Import into CDUMM or copy to your game directory.\n\n"
            f"NOTE: Only ONE mod can use the {_mod_grp}/ slot at a time.\n"
            f"If you already have a {_mod_grp}/ mod, it will be replaced.\n"
            f"Use CDUMM to manage multiple mods, or change the 'Mod:' number.\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Export as Mod",
                                        "Mod name (used as folder name):",
                                        text="My ItemBuffs Mod")
        if not ok or not name.strip():
            return
        name = name.strip()

        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        packs_dir = os.path.join(exe_dir, "packs")
        folder_name = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
        out_path = os.path.join(packs_dir, folder_name)

        apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
        if apply_stacks:
            target_val = self._stack_spin.value()
            for it in self._buff_rust_items:
                if it.get('max_stack_count', 1) > 1:
                    it['max_stack_count'] = target_val

        apply_inf_dura = hasattr(self, '_inf_dura_check') and self._inf_dura_check.isChecked()
        if apply_inf_dura:
            dura_count = 0
            for it in self._buff_rust_items:
                endurance = it.get('max_endurance', 0)
                if endurance > 0 and endurance != 65535:
                    it['max_endurance'] = 65535
                    it['is_destroy_when_broken'] = 0
                    dura_count += 1
            log.info("Infinity Durability: patched %d items", dura_count)

        self._buff_status_label.setText("Serializing with crimson_rs...")
        QApplication.processEvents()

        try:
            import crimson_rs
            import crimson_rs.pack_mod

            _mod_count = 0
            for _it in self._buff_rust_items:
                _psl = _it.get('equip_passive_skill_list', [])
                _edl = _it.get('enchant_data_list', [])
                if _psl:
                    _mod_count += 1
                    log.info("Export: %s has %d passives", _it.get('string_key', '?'), len(_psl))
                for _ed in _edl:
                    _buffs = _ed.get('equip_buffs', [])
                    if len(_buffs) > 1:
                        _mod_count += 1
                        log.info("Export: %s level %d has %d equip_buffs",
                                 _it.get('string_key', '?'), _ed.get('level', 0), len(_buffs))
                        break
            if _mod_count == 0:
                log.warning("Export: NO structural edits found in Rust dicts!")

            final_data = bytearray(crimson_rs.serialize_iteminfo(self._buff_rust_items))
            log.info("Serialized iteminfo: %d bytes", len(final_data))

            self._apply_vfx_changes(final_data)

            cd_patches = getattr(self, '_cd_patches', {})
            if cd_patches:
                cd_hit = 0
                for item_key, (_, _, new_val) in cd_patches.items():
                    cd_off, _ = self._cd_detect(item_key, bytes(final_data))
                    if cd_off is not None:
                        final_data[cd_off:cd_off + 4] = struct.pack('<I', new_val)
                        cd_hit += 1
                log.info("Applied %d/%d cooldown patches to serialized data", cd_hit, len(cd_patches))

            self._apply_transmog_swaps(final_data)

            final_data = bytes(final_data)

        except Exception as e:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, "Serialize Failed",
                f"crimson_rs.serialize_iteminfo() failed:\n{e}")
            return

        self._buff_status_label.setText("Packing with pack_mod...")
        QApplication.processEvents()

        try:
            import tempfile
            import shutil

            game_path = self._buff_patcher.game_path
            mod_group = f"{self._buff_modgroup_spin.value():04d}"

            if os.path.isdir(out_path):
                shutil.rmtree(out_path)
            os.makedirs(out_path, exist_ok=True)

            files_dir = os.path.join(out_path, "files",
                                     "gamedata", "binary__", "client", "bin")
            os.makedirs(files_dir, exist_ok=True)
            with open(os.path.join(files_dir, "iteminfo.pabgb"), "wb") as f:
                f.write(final_data)

            modinfo = {
                "id": name.lower().replace(" ", "_"),
                "name": name,
                "version": "1.0.0",
                "game_version": "1.00.03",
                "author": "CrimsonSaveEditor",
                "description": f"ItemBuffs mod: {name}",
            }
            with open(os.path.join(out_path, "modinfo.json"), "w", encoding="utf-8") as f:
                json.dump(modinfo, f, indent=2)

            data_size = len(final_data)
            self._buff_status_label.setText(
                f"Exported mod to packs/{folder_name}/ ({data_size:,} bytes)")
            QMessageBox.information(self, "Mod Exported",
                f"Mod exported to:\n{out_path}\n\n"
                f"Contents:\n"
                f"  files/gamedata/binary__/client/bin/iteminfo.pabgb ({data_size:,} bytes)\n"
                f"  modinfo.json\n\n"
                f"To install:\n"
                f"  Copy '{folder_name}' into your mod loader's mods/ directory\n"
                f"  (CD JSON Mod Manager, DMM, or CDUMM)")

        except Exception as e:
            import traceback; traceback.print_exc()
            self._buff_status_label.setText(f"Export failed: {e}")
            QMessageBox.critical(self, "Export Failed", str(e))


    def _buff_export_cdumm_mod(self) -> None:
        """Export modified iteminfo as a CDUMM-compatible mod with PAZ packing.

        Uses crimson_rs.serialize_iteminfo() + pack_mod() to generate proper
        PAZ archives. Output structure:
            <ModName>/
                0036/0.paz + 0.pamt
                meta/0.papgt
                modinfo.json
        This is directly importable by CDUMM mod manager.
        """
        if not self._buff_ensure_patcher():
            return

        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Export CDUMM Mod",
                "Extract with Rust parser first (click 'Extract (Rust)').")
            return

        has_cd = bool(getattr(self, '_cd_patches', {}))
        if not self._buff_modified and not has_cd:
            apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
            if not apply_stacks:
                QMessageBox.information(self, "No Changes",
                    "No modifications have been made.\n"
                    "Add buffs, apply God Mode, check 'Max Stacks', or use 'No Cooldown (All Items)' first.")
                return

        _mod_grp = f"{self._buff_modgroup_spin.value():04d}"
        reply = QMessageBox.question(
            self, "Export as CDUMM Mod — PAZ Packed",
            f"This exports a fully packed CDUMM mod folder.\n\n"
            f"WHAT THIS SUPPORTS:\n"
            f"  - Everything 'Export as Mod' can do, PLUS:\n"
            f"  - Proper PAZ archives (0.paz + 0.pamt)\n"
            f"  - PAPGT metadata for game loading\n"
            f"  - Direct import into CDUMM mod manager\n\n"
            f"OUTPUT:\n"
            f"  {_mod_grp}/0.paz + {_mod_grp}/0.pamt\n"
            f"  meta/0.papgt\n"
            f"  modinfo.json\n\n"
            f"REQUIRES: Game path set (needed for pack_mod).\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        from PySide6.QtWidgets import QInputDialog, QFileDialog
        name, ok = QInputDialog.getText(self, "Export as CDUMM Mod",
                                        "Mod name (used as folder name):",
                                        text="My ItemBuffs Mod")
        if not ok or not name.strip():
            return
        name = name.strip()

        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        default_dir = os.path.join(exe_dir, "packs")
        os.makedirs(default_dir, exist_ok=True)
        folder_name = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in name)
        save_dir = QFileDialog.getExistingDirectory(
            self, f"Choose folder to create '{folder_name}' CDUMM mod in", default_dir)
        if not save_dir:
            return
        out_path = os.path.join(save_dir, folder_name)

        apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
        if apply_stacks:
            target_val = self._stack_spin.value()
            for it in self._buff_rust_items:
                if it.get('max_stack_count', 1) > 1:
                    it['max_stack_count'] = target_val

        apply_inf_dura = hasattr(self, '_inf_dura_check') and self._inf_dura_check.isChecked()
        if apply_inf_dura:
            dura_count = 0
            for it in self._buff_rust_items:
                endurance = it.get('max_endurance', 0)
                if endurance > 0 and endurance != 65535:
                    it['max_endurance'] = 65535
                    it['is_destroy_when_broken'] = 0
                    dura_count += 1
            log.info("CDUMM Infinity Durability: patched %d items", dura_count)

        self._buff_status_label.setText("Serializing with crimson_rs...")
        QApplication.processEvents()

        try:
            import crimson_rs
            import crimson_rs.pack_mod

            final_data = bytearray(crimson_rs.serialize_iteminfo(self._buff_rust_items))
            log.info("CDUMM export: serialized iteminfo: %d bytes", len(final_data))

            cd_patches = getattr(self, '_cd_patches', {})
            self._apply_vfx_changes(final_data)

            if cd_patches:
                cd_hit = 0
                for item_key, (_, _, new_val) in cd_patches.items():
                    cd_off, _ = self._cd_detect(item_key, bytes(final_data))
                    if cd_off is not None:
                        final_data[cd_off:cd_off + 4] = struct.pack('<I', new_val)
                        cd_hit += 1
                log.info("Applied %d/%d cooldown patches", cd_hit, len(cd_patches))

            self._apply_transmog_swaps(final_data)

            final_data = bytes(final_data)

        except Exception as e:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, "Serialize Failed",
                f"crimson_rs.serialize_iteminfo() failed:\n{e}")
            return

        self._buff_status_label.setText("Packing with pack_mod...")
        QApplication.processEvents()

        try:
            import tempfile
            import shutil

            game_path = self._buff_patcher.game_path
            mod_group = f"{self._buff_modgroup_spin.value():04d}"

            if os.path.isdir(out_path):
                shutil.rmtree(out_path)
            os.makedirs(out_path, exist_ok=True)

            with tempfile.TemporaryDirectory() as tmp_dir:
                mod_dir = os.path.join(tmp_dir, "gamedata", "binary__", "client", "bin")
                os.makedirs(mod_dir, exist_ok=True)
                with open(os.path.join(mod_dir, "iteminfo.pabgb"), "wb") as f:
                    f.write(final_data)

                pack_out = os.path.join(tmp_dir, "output")
                os.makedirs(pack_out, exist_ok=True)

                crimson_rs.pack_mod.pack_mod(
                    game_dir=game_path,
                    mod_folder=tmp_dir,
                    output_dir=pack_out,
                    group_name=mod_group,
                )

                paz_src = os.path.join(pack_out, mod_group)
                paz_dst = os.path.join(out_path, mod_group)
                os.makedirs(paz_dst, exist_ok=True)
                shutil.copy2(os.path.join(paz_src, "0.paz"), os.path.join(paz_dst, "0.paz"))
                shutil.copy2(os.path.join(paz_src, "0.pamt"), os.path.join(paz_dst, "0.pamt"))

                meta_src = os.path.join(pack_out, "meta", "0.papgt")
                meta_dst = os.path.join(out_path, "meta")
                os.makedirs(meta_dst, exist_ok=True)
                shutil.copy2(meta_src, os.path.join(meta_dst, "0.papgt"))

            modinfo = {
                "id": name.lower().replace(" ", "_"),
                "name": name,
                "version": "1.0.0",
                "game_version": "1.00.03",
                "author": "CrimsonSaveEditor",
                "description": f"ItemBuffs mod: {name}",
            }
            with open(os.path.join(out_path, "modinfo.json"), "w", encoding="utf-8") as f:
                json.dump(modinfo, f, indent=2)

            paz_size = os.path.getsize(os.path.join(paz_dst, "0.paz"))
            self._buff_status_label.setText(
                f"Exported CDUMM mod to {folder_name}/ ({paz_size:,} bytes PAZ)")
            QMessageBox.information(self, "CDUMM Mod Exported",
                f"Mod exported to:\n{out_path}\n\n"
                f"Contents:\n"
                f"  {mod_group}/0.paz ({paz_size:,} bytes)\n"
                f"  {mod_group}/0.pamt\n"
                f"  meta/0.papgt\n"
                f"  modinfo.json\n\n"
                f"To install:\n"
                f"  Import the '{folder_name}' folder into CDUMM,\n"
                f"  or copy the contents to your game directory.")

        except Exception as e:
            import traceback; traceback.print_exc()
            self._buff_status_label.setText(f"CDUMM export failed: {e}")
            QMessageBox.critical(self, "Export Failed",
                f"pack_mod failed:\n{e}\n\n"
                f"Make sure the game path is set correctly.")


    def _buff_save_config(self) -> None:
        """Save current edits as a reusable config file.

        Diffs current Rust dicts against vanilla to capture only the changes.
        The config is a recipe — it describes WHAT to change, not raw bytes.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Save Config",
                "Extract with Rust parser first (click 'Extract (Rust)').")
            return

        if not self._buff_modified:
            apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
            if not apply_stacks:
                QMessageBox.information(self, "Save Config",
                    "No modifications to save.")
                return

        try:
            import crimson_rs
            vanilla_items = crimson_rs.parse_iteminfo_from_bytes(
                self._buff_patcher._original_data)
            vanilla_lookup = {it['key']: it for it in vanilla_items}
        except Exception as e:
            QMessageBox.critical(self, "Save Config",
                f"Failed to parse vanilla data for diff:\n{e}")
            return

        items_config = {}
        for item in self._buff_rust_items:
            key = item['key']
            vanilla = vanilla_lookup.get(key)
            if not vanilla:
                continue

            item_changes = {}

            if item.get('equip_passive_skill_list') != vanilla.get('equip_passive_skill_list'):
                item_changes['equip_passive_skill_list'] = item.get('equip_passive_skill_list', [])

            for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                        'max_charged_useable_count', 'docking_child_data',
                        'respawn_time_seconds'):
                if item.get(gf) != vanilla.get(gf):
                    item_changes[gf] = item.get(gf)

            if item.get('max_stack_count') != vanilla.get('max_stack_count'):
                item_changes['max_stack_count'] = item['max_stack_count']

            v_edl = vanilla.get('enchant_data_list', [])
            m_edl = item.get('enchant_data_list', [])
            if v_edl and m_edl and len(v_edl) == len(m_edl):
                enchant_changes = {}
                for i, (v_ed, m_ed) in enumerate(zip(v_edl, m_edl)):
                    level_changes = {}

                    if m_ed.get('equip_buffs') != v_ed.get('equip_buffs'):
                        level_changes['equip_buffs'] = m_ed.get('equip_buffs', [])

                    v_sd = v_ed.get('enchant_stat_data', {})
                    m_sd = m_ed.get('enchant_stat_data', {})
                    for stat_field in ['stat_list_static', 'stat_list_static_level',
                                       'regen_stat_list', 'max_stat_list']:
                        if m_sd.get(stat_field) != v_sd.get(stat_field):
                            level_changes.setdefault('enchant_stat_data', {})[stat_field] = \
                                m_sd.get(stat_field, [])

                    if level_changes:
                        enchant_changes[str(i)] = level_changes

                if enchant_changes:
                    item_changes['enchant_levels'] = enchant_changes

            if item_changes:
                item_changes['string_key'] = item.get('string_key', '')
                items_config[str(key)] = item_changes

        if not items_config:
            QMessageBox.information(self, "Save Config",
                "No differences found between current edits and vanilla.")
            return

        config = {
            "format": "crimson_itembuffs_config",
            "version": 1,
            "name": "",
            "description": f"{len(items_config)} item(s) modified",
            "items": items_config,
        }

        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Save Config",
                                        "Config name:", text="My ItemBuffs Config")
        if not ok or not name.strip():
            return
        config["name"] = name.strip()

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Config", f"{name.strip()}.json",
            "Config Files (*.json)")
        if not path:
            return

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2)

        summary_parts = []
        for key, changes in items_config.items():
            skey = changes.get('string_key', key)
            parts = []
            if 'equip_passive_skill_list' in changes:
                parts.append(f"{len(changes['equip_passive_skill_list'])} passives")
            if 'enchant_levels' in changes:
                n_buffs = 0
                n_stats = 0
                for lv_changes in changes['enchant_levels'].values():
                    n_buffs += len(lv_changes.get('equip_buffs', []))
                    for sd in lv_changes.get('enchant_stat_data', {}).values():
                        n_stats += len(sd)
                if n_buffs:
                    parts.append(f"{n_buffs} buffs")
                if n_stats:
                    parts.append(f"{n_stats} stats")
            if 'max_stack_count' in changes:
                parts.append(f"stack={changes['max_stack_count']}")
            summary_parts.append(f"  {skey}: {', '.join(parts)}")

        self._buff_status_label.setText(f"Config saved: {os.path.basename(path)}")
        if len(summary_parts) > 6:
            shown = "\n".join(summary_parts[:6])
            shown += f"\n  ... and {len(summary_parts) - 6} more items"
        else:
            shown = "\n".join(summary_parts)
        QMessageBox.information(self, "Config Saved",
            f"Saved to:\n{path}\n\n"
            f"Changes ({len(summary_parts)} items):\n{shown}\n\n"
            f"Share this file or load it later to tweak and re-export.")


    def _buff_load_config(self) -> None:
        """Load a config file and re-apply edits to the current Rust dicts."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Load Config",
                "Extract with Rust parser first (click 'Extract (Rust)').\n"
                "The config will be applied on top of fresh game data.")
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Load Config", "",
            "Config Files (*.json);;All Files (*)")
        if not path:
            return

        try:
            with open(path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Load Config", f"Failed to read file:\n{e}")
            return

        if config.get('format') != 'crimson_itembuffs_config':
            QMessageBox.warning(self, "Load Config",
                "This doesn't look like an ItemBuffs config file.\n"
                f"Expected format 'crimson_itembuffs_config', got '{config.get('format', '?')}'.")
            return

        items_config = config.get('items', {})
        if not items_config:
            QMessageBox.information(self, "Load Config", "Config has no item changes.")
            return

        reply = QMessageBox.question(
            self, "Load Config",
            f"Loading config: {config.get('name', 'unnamed')}\n"
            f"Contains {len(items_config)} item edit(s).\n\n"
            f"This will RESET current edits and apply the config\n"
            f"on top of fresh vanilla data.\n\n"
            f"Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            import crimson_rs
            fresh = crimson_rs.parse_iteminfo_from_bytes(
                self._buff_patcher._original_data)
            self._buff_rust_items = fresh
            self._buff_rust_lookup = {it['key']: it for it in fresh}
        except Exception as e:
            QMessageBox.critical(self, "Load Config",
                f"Failed to re-parse vanilla data:\n{e}")
            return

        applied = 0
        skipped = []
        for key_str, changes in items_config.items():
            key = int(key_str)
            rust_info = self._buff_rust_lookup.get(key)
            if not rust_info:
                skipped.append(changes.get('string_key', key_str))
                continue

            if 'equip_passive_skill_list' in changes:
                rust_info['equip_passive_skill_list'] = changes['equip_passive_skill_list']

            for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                        'max_charged_useable_count', 'docking_child_data',
                        'respawn_time_seconds'):
                if gf in changes:
                    rust_info[gf] = changes[gf]

            if 'max_stack_count' in changes:
                rust_info['max_stack_count'] = changes['max_stack_count']

            if 'enchant_levels' in changes:
                edl = rust_info.get('enchant_data_list', [])
                for lvl_str, lv_changes in changes['enchant_levels'].items():
                    lvl_idx = int(lvl_str)
                    if lvl_idx >= len(edl):
                        continue

                    ed = edl[lvl_idx]

                    if 'equip_buffs' in lv_changes:
                        ed['equip_buffs'] = lv_changes['equip_buffs']

                    if 'enchant_stat_data' in lv_changes:
                        sd = ed.setdefault('enchant_stat_data', {})
                        for stat_field, stat_list in lv_changes['enchant_stat_data'].items():
                            sd[stat_field] = stat_list

            applied += 1

        try:
            import crimson_rs
            new_data = crimson_rs.serialize_iteminfo(self._buff_rust_items)
            self._buff_data = bytearray(new_data)
            self._buff_rust_items = crimson_rs.parse_iteminfo_from_bytes(new_data)
            self._buff_rust_lookup = {it['key']: it for it in self._buff_rust_items}
            self._buff_items = self._buff_patcher.find_items(bytes(self._buff_data))
            log.info("Load Config: synced byte buffer (%d bytes)", len(new_data))
        except Exception as e:
            log.warning("Load Config: byte buffer sync failed: %s", e)

        self._buff_modified = True
        self._buff_refresh_stats()

        msg = f"Applied config to {applied} item(s)."
        if skipped:
            if len(skipped) > 6:
                shown = ', '.join(skipped[:6]) + f' ... +{len(skipped)-6} more'
            else:
                shown = ', '.join(skipped)
            msg += f"\nSkipped {len(skipped)} item(s) not found in game data: {shown}"

        self._buff_status_label.setText(
            f"Loaded config: {config.get('name', '')} ({applied} items). "
            f"Click 'Export as Mod' to write.")
        QMessageBox.information(self, "Config Loaded", msg)


    def _buff_on_stat_selected(self, *_args) -> None:
        """Update the edit-selected controls when a stat row is clicked."""
        rows = self._buff_stats_table.selectionModel().selectedRows()
        if not rows:
            self._buff_sel_label.setText("")
            return
        row = rows[0].row()
        item = self._buff_stats_table.item(row, 0)
        if not item:
            return

        kind_data = item.data(Qt.UserRole + 1)
        if kind_data:
            kind = kind_data[0]
            if kind == 'passive':
                key = kind_data[1]
                for i in range(self._eb_passive_combo.count()):
                    if self._eb_passive_combo.itemData(i) == key:
                        self._eb_passive_combo.setCurrentIndex(i)
                        break
                name = self._PASSIVE_SKILL_NAMES.get(key, f"Skill {key}")
                self._eb_status.setText(f"Selected: {name} — click Remove to delete")
                self._buff_sel_label.setText("(passive row — use Remove button above)")
                return
            elif kind == 'buff':
                key = kind_data[1]
                for i in range(self._eb_buff_combo.count()):
                    if self._eb_buff_combo.itemData(i) == key:
                        self._eb_buff_combo.setCurrentIndex(i)
                        break
                name = self._EQUIP_BUFF_NAMES.get(key, f"Buff {key}")
                self._eb_status.setText(f"Selected: {name} — click Remove Buff to delete")
                self._buff_sel_label.setText("(buff row — use Remove Buff button above)")
                return
            elif kind == 'stat':
                stat_key_val = kind_data[1]
                stat_list_name = kind_data[2]
                raw_val = kind_data[3] if len(kind_data) > 3 else None
                if raw_val is not None:
                    self._eb_stat_value.setValue(raw_val)
                for i in range(self._eb_stat_combo.count()):
                    idx = self._eb_stat_combo.itemData(i)
                    if idx is not None and idx < len(self._ENCHANT_STAT_LIST):
                        _, skey, slist, _ = self._ENCHANT_STAT_LIST[idx]
                        if skey == stat_key_val and slist == stat_list_name:
                            self._eb_stat_combo.setCurrentIndex(i)
                            sname = self._ENCHANT_STAT_LIST[idx][0]
                            self._eb_status.setText(f"Selected: {sname} (current value: {raw_val:,}) — click Remove to delete")
                            self._buff_sel_label.setText("(stat row — use Stat Remove button above)")
                            return
                self._buff_sel_label.setText(f"(stat {stat_key_val} in {stat_list_name} — not in Remove list)")
                return

        entry = item.data(Qt.UserRole)
        if not entry:
            self._buff_sel_label.setText("(header row — select a stat)")
            return
        self._buff_sel_label.setText(f"{entry.name} [{entry.size_class}]")
        self._buff_sel_value_spin.setValue(entry.value)


    _CD_MARKER = b'\x00\x00\x00\x00\x00\x00\x00\x0e'


    def _cd_detect(self, item_key: int, raw: bytes = None):
        """Return (abs_offset, current_seconds) for the cooldown field of item_key,
        or (None, None) if not found.

        Algorithm: find item entry in iteminfo.pabgb by name, then scan up to
        2000 bytes for the signature: 8-byte marker + [cd_u32] + 4 zero bytes.
        Verified against 48 items from Pldada's no-cooldown mod JSON.

        Pass raw= to search in a different binary (e.g. freshly serialized data).
        """
        if raw is None:
            if not hasattr(self, '_buff_patcher') or self._buff_patcher is None:
                return None, None
            raw = self._buff_patcher._original_data
        if raw is None:
            return None, None

        item_name = None
        if hasattr(self, '_buff_items') and self._buff_items:
            for it in self._buff_items:
                if it.item_key == item_key:
                    item_name = it.name
                    break
        if not item_name:
            return None, None

        nb = item_name.encode('utf-8')
        pos = raw.find(nb)
        while pos != -1:
            check = pos - 8
            if check >= 0:
                nlen_check = struct.unpack_from('<I', raw, check + 4)[0]
                if nlen_check == len(nb) and (pos + len(nb) < len(raw)) and raw[pos + len(nb)] == 0:
                    entry_start = check
                    break
            pos = raw.find(nb, pos + 1)
        else:
            return None, None

        scan_end = min(entry_start + 2000, len(raw) - 16)
        i = entry_start
        while i < scan_end:
            idx = raw.find(self._CD_MARKER, i, scan_end)
            if idx == -1:
                break
            cd_off = idx + 8
            cd_val = struct.unpack_from('<I', raw, cd_off)[0]
            after_val = struct.unpack_from('<I', raw, cd_off + 4)[0]
            if 1 <= cd_val <= 86400 and after_val == 0:
                return cd_off, cd_val
            i = idx + 1
        return None, None


    def _buff_open_desc_search(self):
        """Open the description search dialog for buffs/passives/effects."""
        dlg = DescriptionSearchDialog(parent=self)
        if dlg.exec() == QDialog.Accepted and dlg.selected_key:
            QApplication.clipboard().setText(str(dlg.selected_key))
            self._buff_status_label.setText(
                f"Selected: {dlg.selected_name} (key {dlg.selected_key}, type: {dlg.selected_type}) — copied to clipboard")


    def _max_charges_all_items(self) -> None:
        """Set max_charged_useable_count on every activated item in the Rust dicts."""
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "Max Charges", "Extract with Rust parser first.")
            return

        target = self._max_charges_spin.value()
        patched = 0
        skipped_passive = 0
        skipped_unchanged = 0
        for it in self._buff_rust_items:
            if it.get('item_charge_type', 0) != 0:
                skipped_passive += 1
                continue
            cur = it.get('max_charged_useable_count', 0) or 0
            if cur == target:
                skipped_unchanged += 1
                continue
            if cur == 0:
                continue
            it['max_charged_useable_count'] = target
            patched += 1

        self._buff_modified = True
        QMessageBox.information(
            self, "Max Charges — Done",
            f"Set max_charged_useable_count = {target} on {patched} item(s).\n"
            f"Skipped: {skipped_passive} passive items, {skipped_unchanged} already at target.\n\n"
            f"Note: Only FRESH copies (new drops/crafts) will actually have the new\n"
            f"charge count. Items already in your save keep their current value.\n\n"
            f"Use Export as Mod to write."
        )


    def _cd_patch_all_items(self) -> None:
        """Queue cooldown → 1s for every item in iteminfo that has a cooldown field."""
        if not hasattr(self, '_buff_patcher') or self._buff_patcher is None:
            QMessageBox.warning(self, "No Cooldown", "Extract iteminfo first.")
            return
        if not hasattr(self, '_buff_items') or not self._buff_items:
            QMessageBox.warning(self, "No Cooldown", "Extract iteminfo first.")
            return

        raw = self._buff_patcher._original_data
        patched = 0
        skipped = 0
        for item in self._buff_items:
            if item.item_key in self._cd_patches:
                skipped += 1
                continue
            abs_off, cur_val = self._cd_detect(item.item_key)
            if abs_off is None:
                continue
            if cur_val == 1:
                continue
            orig_bytes = bytes(raw[abs_off:abs_off + 4])
            self._cd_patches[item.item_key] = (abs_off, orig_bytes, 1)
            patched += 1

        self._buff_modified = True

        has_stat_edits = self._buff_modified and bool(
            getattr(self, '_buff_rust_items', None) and
            any(
                it.get('equip_passive_skill_list') or
                any(ed.get('equip_buffs') or ed.get('enchant_stat_data')
                    for ed in it.get('enchant_data_list', []))
                for it in self._buff_rust_items
            )
        )

        skip_note = f"\n{skipped} item(s) were already queued and skipped." if skipped else ""
        export_hint = (
            "\n\nYou also have stat/passive edits — use:\n"
            "  • Export as Mod  (includes everything)\n"
            "  • Export JSON Patch  (value-only edits)"
            if has_stat_edits else
            "\n\nUse Export JSON Patch or Export as Mod to apply."
        )

        QMessageBox.information(
            self, "No Cooldown — Done",
            f"Queued {patched} cooldown patch(es) (→ 1s).{skip_note}{export_hint}"
        )


    def _buff_apply_to_selected(self) -> None:
        """Apply a value change to ONLY the selected stat entry.

        Operates on Rust dicts (single source of truth) — not byte buffer.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "No Data", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            return

        rows = self._buff_stats_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "No Selection", "Click a stat row in the table first.")
            return
        row = rows[0].row()
        item_widget = self._buff_stats_table.item(row, 0)
        if not item_widget:
            return

        kind_data = item_widget.data(Qt.UserRole + 1)
        if not kind_data or kind_data[0] != 'stat':
            QMessageBox.information(self, "No Stat",
                "Select a stat entry (not a header, passive, or buff row).")
            return

        stat_key = kind_data[1]
        stat_list_name = kind_data[2]
        old_value = kind_data[3] if len(kind_data) > 3 else 0

        new_value = self._buff_sel_value_spin.value()
        if new_value == old_value:
            return

        stat_name = self._buff_sel_label.text() if hasattr(self, '_buff_sel_label') else f"Stat {stat_key}"

        reply = QMessageBox.question(
            self, "Edit Single Stat",
            f"Change {stat_name} from {old_value:,} to {new_value:,}?\n\n"
            f"Only THIS stat entry will be modified.\n"
            f"All other stats remain untouched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        edl = rust_info.get('enchant_data_list', [])
        target_level = 0
        if hasattr(self, '_eb_level_target'):
            sel = self._eb_level_target.currentData()
            if sel is not None and sel >= 0:
                target_level = sel

        if target_level < len(edl):
            sd = edl[target_level].setdefault('enchant_stat_data', {})
            existing = sd.get(stat_list_name, [])
            for i, s in enumerate(existing):
                if s['stat'] == stat_key:
                    existing[i] = {'stat': stat_key, 'change_mb': new_value}
                    break
            sd[stat_list_name] = existing

        self._buff_modified = True
        self._buff_refresh_stats()
        self._buff_status_label.setText(
            f"Changed {stat_name}: {old_value:,} -> {new_value:,}. Click 'Export as Mod' to write."
        )


    def _buff_add_to_item(self) -> None:
        """Apply the selected preset to the currently selected item.

        Modifies Rust dicts directly — single source of truth.
        All presets, including Max All and Custom, operate on enchant_stat_data.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "No Data", "Extract with Rust parser first.")
            return

        item = self._buff_current_item
        if item is None:
            QMessageBox.information(self, "No Item Selected",
                                    "Select an item from the search results first.")
            return

        rust_info = self._buff_rust_lookup.get(item.item_key)
        if not rust_info:
            return

        edl = rust_info.get('enchant_data_list', [])
        if not edl:
            QMessageBox.warning(self, "No Enchant Data",
                                "This item has no enchant data to modify.")
            return

        target_level = self._eb_level_target.currentData() if hasattr(self, '_eb_level_target') else -1

        preset_idx = self._buff_preset_combo.currentIndex()
        preset_name = ""
        modified = 0

        for idx, ed in enumerate(edl):
            if target_level != -1 and idx != target_level:
                continue

            sd = ed.setdefault('enchant_stat_data', {})

            if preset_idx == 0:
                preset_name = "Max All"
                for list_name in ['stat_list_static', 'stat_list_static_level', 'regen_stat_list', 'max_stat_list']:
                    for s in sd.get(list_name, []):
                        if list_name == 'stat_list_static_level':
                            s['change_mb'] = 15
                        else:
                            s['change_mb'] = 999_999
                modified += 1

            elif preset_idx == 1:
                preset_name = "Max All Flat"
                for s in sd.get('stat_list_static', []):
                    s['change_mb'] = 999_999
                modified += 1

            elif preset_idx == 2:
                preset_name = "Max DDD"
                self._set_stat(sd, 'stat_list_static', 1000002, 999_999)
                modified += 1

            elif preset_idx == 3:
                preset_name = "Max DPV"
                self._set_stat(sd, 'stat_list_static', 1000003, 999_999)
                modified += 1

            elif preset_idx == 4:
                preset_name = "Max HP"
                self._set_stat(sd, 'stat_list_static', 1000000, 999_999)
                modified += 1

            elif preset_idx == 5:
                preset_name = "Max All Rates"
                for s in sd.get('stat_list_static_level', []):
                    s['change_mb'] = 15
                modified += 1

            elif preset_idx == 6:
                preset_name = "Swap to DDD"
                for s in sd.get('stat_list_static', []):
                    s['stat'] = 1000002
                modified += 1

            elif preset_idx == 7:
                preset_name = "Swap to DPV"
                for s in sd.get('stat_list_static', []):
                    s['stat'] = 1000003
                modified += 1

            else:
                buff_name = self._buff_type_combo.currentText()
                buff_hash = BUFF_HASHES.get(buff_name)
                if buff_hash is None:
                    continue
                value = self._buff_value_spin.value()
                preset_name = f"Custom: {buff_name}={value}"

                from paz_patcher import _stat_size_class
                size_class = _stat_size_class(buff_hash)
                if size_class == 'flat2':
                    self._set_stat(sd, 'stat_list_static', buff_hash, value)
                elif size_class == 'rate':
                    self._set_stat(sd, 'stat_list_static_level', buff_hash, value)
                else:
                    self._set_stat(sd, 'stat_list_static', buff_hash, value)
                modified += 1

        display_name = self._name_db.get_name(item.item_key)
        if display_name.startswith("Unknown"):
            display_name = item.name

        self._buff_modified = True
        self._buff_refresh_stats()
        level_str = f"level +{target_level}" if target_level >= 0 else f"{modified} levels"
        self._buff_status_label.setText(
            f"Applied '{preset_name}' to {display_name} ({level_str}). "
            f"Click 'Export as Mod' to write."
        )


    def _buff_sync_to_rust(self, item_key: int = None) -> None:
        """Re-parse byte buffer into Rust dicts so serialize_items() gets byte-level edits.

        IMPORTANT: Preserves structural edits (added buffs, passives, stats)
        that only exist in _buff_rust_items, not in the byte buffer.
        Only byte-level value changes from _buff_data are synced.
        """
        if not hasattr(self, '_buff_rust_items') or self._buff_data is None:
            return
        try:
            import crimson_rs

            saved_structural = {}
            for it in self._buff_rust_items:
                key = it['key']
                structural = {}

                psl = it.get('equip_passive_skill_list', [])
                if psl:
                    structural['equip_passive_skill_list'] = psl

                for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                            'max_charged_useable_count', 'docking_child_data'):
                    val = it.get(gf)
                    if val is not None:
                        structural[gf] = val

                edl = it.get('enchant_data_list', [])
                if edl:
                    structural['enchant_data_list'] = [
                        {
                            'equip_buffs': ed.get('equip_buffs'),
                            'enchant_stat_data': ed.get('enchant_stat_data'),
                        }
                        for ed in edl
                    ]

                if structural:
                    saved_structural[key] = structural

            fresh = crimson_rs.parse_iteminfo_from_bytes(bytes(self._buff_data))

            fresh_lookup = {it['key']: it for it in fresh}
            for key, structural in saved_structural.items():
                fi = fresh_lookup.get(key)
                if not fi:
                    continue

                if 'equip_passive_skill_list' in structural:
                    fi['equip_passive_skill_list'] = structural['equip_passive_skill_list']

                for gf in ('gimmick_info', 'cooltime', 'item_charge_type',
                            'max_charged_useable_count', 'docking_child_data'):
                    if gf in structural:
                        fi[gf] = structural[gf]

                if 'enchant_data_list' in structural:
                    fi_edl = fi.get('enchant_data_list', [])
                    for i, saved_ed in enumerate(structural['enchant_data_list']):
                        if i >= len(fi_edl):
                            break
                        if saved_ed.get('equip_buffs') is not None:
                            fi_edl[i]['equip_buffs'] = saved_ed['equip_buffs']
                        if saved_ed.get('enchant_stat_data') is not None:
                            fi_edl[i]['enchant_stat_data'] = saved_ed['enchant_stat_data']

            self._buff_rust_items = fresh
            self._buff_rust_lookup = {it['key']: it for it in fresh}
        except Exception as e:
            log.warning("Rust re-parse failed: %s", e)


    def _buff_remove_all(self) -> None:
        """Reset all in-memory modifications by re-parsing from original data."""
        if self._buff_patcher is None:
            return

        reply = QMessageBox.question(
            self, "Reset All Changes",
            "Discard all in-memory modifications?\n\n"
            "This returns everything to the extracted vanilla state.\n"
            "No files on disk are modified.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            if hasattr(self, '_buff_rust_items_original') and self._buff_rust_items_original:
                import copy
                self._buff_rust_items = copy.deepcopy(self._buff_rust_items_original)
                self._buff_rust_lookup = {it['key']: it for it in self._buff_rust_items}
                original = self._buff_patcher._original_data
                if original:
                    self._buff_data = bytearray(original)
                    self._buff_items = self._buff_patcher.find_items(bytes(original))
            else:
                original = self._buff_patcher._original_data
                if original:
                    self._buff_data = bytearray(original)
                    self._buff_items = self._buff_patcher.find_items(bytes(original))
                else:
                    raw = self._buff_patcher.extract_iteminfo()
                    self._buff_data = bytearray(raw)
                    self._buff_items = self._buff_patcher.find_items(bytes(raw))
                try:
                    import crimson_rs
                    self._buff_rust_items = crimson_rs.parse_iteminfo_from_bytes(bytes(self._buff_data))
                    self._buff_rust_lookup = {it['key']: it for it in self._buff_rust_items}
                except Exception:
                    pass

            self._buff_modified = False
            self._buff_current_item = None
            try:
                self._buff_stats_table.setRowCount(0)
            except RuntimeError:
                pass
            if hasattr(self, '_buff_selected_label'):
                try:
                    self._buff_selected_label.setText("No item selected — search and click an item on the left")
                    self._buff_selected_label.setStyleSheet(
                        f"color: {COLORS['text_dim']}; font-weight: bold; padding: 2px 4px;"
                    )
                except RuntimeError:
                    pass
            try:
                self._buff_status_label.setText("Reset complete — vanilla state restored.")
            except RuntimeError:
                pass
        except Exception as e:
            try:
                QMessageBox.critical(self, "Reset Failed", str(e))
            except RuntimeError:
                pass


    def _buff_remove_selected(self) -> None:
        """Remove the selected stat from the item.

        Operates on Rust dicts (single source of truth) — not byte buffer.
        """
        if not hasattr(self, '_buff_rust_items') or not self._buff_rust_items:
            QMessageBox.warning(self, "No Data", "Extract with Rust parser first.")
            return
        if not hasattr(self, '_buff_current_item') or self._buff_current_item is None:
            return

        rows = self._buff_stats_table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(
                self, "No Selection",
                "Select a stat entry from the table to remove.",
            )
            return

        row = rows[0].row()
        name_cell = self._buff_stats_table.item(row, 0)
        if name_cell is None:
            return

        kind_data = name_cell.data(Qt.UserRole + 1)
        if not kind_data or kind_data[0] != 'stat':
            QMessageBox.information(self, "Not a Stat",
                "Select a stat entry (not a header, passive, or buff row).\n"
                "Use the Remove buttons above for passives and buffs.")
            return

        stat_key = kind_data[1]
        stat_list_name = kind_data[2]
        stat_value = kind_data[3] if len(kind_data) > 3 else 0

        _STAT_NAMES = {
            1000000: "HP", 1000002: "DDD", 1000003: "DPV",
            1000006: "Crit Damage", 1000007: "Crit Rate",
            1000010: "Attack Speed", 1000011: "Move Speed",
        }
        stat_name = (getattr(self, '_STAT_NAMES_COMMUNITY', {}).get(stat_key)
                     or _STAT_NAMES.get(stat_key, f"Stat {stat_key}"))

        reply = QMessageBox.question(
            self, "Remove Stat",
            f"Remove '{stat_name}' (value={stat_value:,}) from this item?\n\n"
            f"Removes from ALL enchant levels.\n"
            f"The change is held in memory until you click 'Export as Mod'.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        rust_info = self._buff_rust_lookup.get(self._buff_current_item.item_key)
        if not rust_info:
            return

        edl = rust_info.get('enchant_data_list', [])
        removed = 0
        for ed in edl:
            sd = ed.get('enchant_stat_data', {})
            existing = sd.get(stat_list_name, [])
            new_list = [s for s in existing if s['stat'] != stat_key]
            if len(new_list) < len(existing):
                removed += 1
                sd[stat_list_name] = new_list

        self._buff_modified = True
        self._buff_refresh_stats()
        display_name = self._name_db.get_name(self._buff_current_item.item_key)
        self._buff_status_label.setText(
            f"Removed {stat_name} from '{display_name}' ({removed} levels). "
            f"Click 'Export as Mod' to write."
        )


    def _buff_max_stacks(self, target: int = 9999) -> None:
        """Patch all stackable items to target max stack and write to game."""
        game_path = self._pabgb_get_game_path() if hasattr(self, '_pabgb_get_game_path') else self._config.get("game_install_path", "")
        if not game_path:
            QMessageBox.warning(self, "No Game Path", "Set the game install path using the Browse button at the top.")
            return

        if not _is_admin():
            QMessageBox.warning(
                self, "Admin Required",
                "Writing to game files requires administrator privileges.\n\n"
                "Right-click → Run as administrator",
            )
            return

        reply = QMessageBox.question(
            self, f"Max Stacks ({target})",
            f"Set all stackable item max stacks to {target}?\n\n"
            "This modifies iteminfo.pabgb in the game files.\n"
            "Equipment and non-stackable items are NOT affected.\n"
            "Replaces the FatStacks mod — no external mod needed.\n\n"
            "A backup will be created automatically.\n"
            "Survives game updates (structural parsing, no hardcoded offsets).",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._buff_status_label.setText("Preparing iteminfo...")
        QApplication.processEvents()

        try:
            if not self._buff_ensure_patcher():
                return

            if self._buff_data is not None:
                data = self._buff_data
            else:
                raw = self._buff_patcher.extract_iteminfo()
                data = bytearray(raw)
                self._buff_data = data
                self._buff_items = self._buff_patcher.find_items(bytes(data))

            self._buff_status_label.setText("Patching stack sizes...")
            QApplication.processEvents()

            count, descriptions = self._buff_patcher.patch_stack_sizes(data, target_stack=target)

            if count == 0:
                QMessageBox.information(self, "No Changes", f"All items already at {target} or are non-stackable.")
                self._buff_status_label.setText("No stack changes needed.")
                return

            self._buff_modified = True
            self._buff_status_label.setText(
                f"Stack sizes set to {target} for {count} items. "
                f"Click 'Export JSON Patch' to write."
            )
            QMessageBox.information(
                self, "Stacks Patched",
                f"Patched {count} items to {target} max stack (in memory).\n\n"
                f"Click 'Export JSON Patch' to write all changes to disk.",
            )

        except Exception as e:
            self._buff_status_label.setText(f"Error: {e}")
            QMessageBox.critical(self, "Error", str(e))


    def _buff_sync_community_names(self) -> None:
        """Download latest buff/stat/passive names from GitHub community repo."""
        BUFF_NAMES_URL = (
            "https://raw.githubusercontent.com/"
            "NattKh/CrimsonDesertCommunityItemMapping/main/buff_names_community.json"
        )
        self._buff_status_label.setText("Syncing buff names from GitHub...")
        QApplication.processEvents()

        try:
            from urllib.request import urlopen, Request
            req = Request(BUFF_NAMES_URL, headers={"User-Agent": "CrimsonSaveEditor/3.0"})
            with urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self._buff_status_label.setText(f"Sync failed: {e}")
            QMessageBox.warning(self, "Sync Failed", f"Could not download buff names:\n{e}")
            return

        updated_buffs = 0
        updated_stats = 0
        updated_passives = 0

        def build_display(name: str, effect: str) -> str:
            if effect and effect != name:
                return f"{name} — {effect[:40]}"
            return name

        for entry in data.get("buffs", []):
            key = entry.get("key", 0)
            name = entry.get("name", "")
            effect = entry.get("effect", "")
            if key <= 0 or not name:
                continue
            display = build_display(name, effect)
            if key not in self._EQUIP_BUFF_NAMES or self._EQUIP_BUFF_NAMES[key] != display:
                self._EQUIP_BUFF_NAMES[key] = display
                updated_buffs += 1
            mn = entry.get("minValue")
            mx = entry.get("maxValue")
            vt = entry.get("valueType", "")
            if mn is not None and mx is not None:
                self._buff_community_ranges[key] = (mn, mx, vt)

        if hasattr(self, '_PASSIVE_SKILL_NAMES'):
            for entry in data.get("passives", []):
                key = entry.get("key", 0)
                name = entry.get("name", "")
                effect = entry.get("effect", "")
                if key <= 0 or not name:
                    continue
                display = build_display(name, effect)
                cur = self._PASSIVE_SKILL_NAMES.get(key)
                cur_str = cur if isinstance(cur, str) else (
                    cur.get('suffix') or cur.get('english_name') if isinstance(cur, dict) else None)
                if cur_str != display:
                    self._PASSIVE_SKILL_NAMES[key] = display
                    updated_passives += 1

        if not hasattr(self, '_STAT_NAMES_COMMUNITY'):
            self._STAT_NAMES_COMMUNITY = {}
        for entry in data.get("stats", []):
            key = entry.get("key", 0)
            name = entry.get("name", "")
            effect = entry.get("effect", "")
            if key <= 0 or not name:
                continue
            display = build_display(name, effect)
            if self._STAT_NAMES_COMMUNITY.get(key) != display:
                self._STAT_NAMES_COMMUNITY[key] = display
                updated_stats += 1
            mn = entry.get("minValue")
            mx = entry.get("maxValue")
            vt = entry.get("valueType", "")
            if mn is not None and mx is not None:
                self._buff_community_ranges[key] = (mn, mx, vt)

        updated = updated_buffs + updated_stats + updated_passives

        try:
            exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
            local_path = os.path.join(exe_dir, "buff_names_community.json")
            with open(local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        if hasattr(self, '_eb_buff_combo'):
            self._eb_buff_combo.clear()
            for bk in sorted(self._EQUIP_BUFF_NAMES.keys()):
                bname = self._EQUIP_BUFF_NAMES[bk]
                desc = self._buff_skill_descs.get(str(bk), {}).get("description", "")
                label = f"{bname} ({bk})" + (f" — {desc}" if desc else "")
                self._eb_buff_combo.addItem(label, bk)

        if hasattr(self, '_eb_passive_combo'):
            self._eb_passive_combo.clear()
            for pk in sorted(self._PASSIVE_SKILL_NAMES.keys()):
                pname = self._PASSIVE_SKILL_NAMES[pk]
                if isinstance(pname, dict):
                    pname = pname.get('suffix') or pname.get('english_name') or str(pk)
                self._eb_passive_combo.addItem(f"{pname} ({pk})", pk)

        try:
            self._buff_refresh_stats()
        except Exception:
            pass

        v = data.get("version", "?")
        stats_count = len(data.get("stats", []))
        buffs_count = len(data.get("buffs", []))
        passives_count = len(data.get("passives", []))
        self._buff_status_label.setText(
            f"Synced v{v}: +{updated_stats} stats, +{updated_buffs} buffs, "
            f"+{updated_passives} passives")

        if updated > 0:
            QMessageBox.information(self, "Community Names Synced",
                f"Updated display names from community database.\n\n"
                f"Version: {v}\n"
                f"  Stats updated:    {updated_stats} / {stats_count}\n"
                f"  Buffs updated:    {updated_buffs} / {buffs_count}\n"
                f"  Passives updated: {updated_passives} / {passives_count}\n\n"
                f"Changes reflect in the stats table, Add Buff/Passive dropdowns,\n"
                f"and description search immediately.\n\n"
                f"Contribute corrections:\n"
                f"github.com/NattKh/CrimsonDesertCommunityItemMapping")
        else:
            QMessageBox.information(self, "Already Up to Date",
                "All names match the latest community database.")


    def _buff_import_community_json(self) -> None:
        """Import a Pldada/DMM-format JSON byte patch into extracted iteminfo.

        Applies byte-level patches to the raw vanilla data, then re-parses
        with crimson_rs. This merges community mods (e.g. Infinity Durability)
        with any ItemBuffs changes the user makes afterwards.
        """
        if not hasattr(self, '_buff_data') or self._buff_data is None:
            QMessageBox.warning(self, "No Data",
                "Extract iteminfo first (click Extract Rust).")
            return

        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Community JSON Patch",
            os.path.dirname(__file__),
            "JSON Files (*.json)")
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                mod_data = json.load(f)

            patches = mod_data.get("patches", [])
            if not patches:
                QMessageBox.warning(self, "Invalid Format",
                    "No 'patches' array found in JSON file.\n"
                    "Expected Pldada/DMM format with patches[].changes[].")
                return

            changes = []
            for patch_block in patches:
                game_file = patch_block.get("game_file", "")
                if "iteminfo" in game_file.lower():
                    changes = patch_block.get("changes", [])
                    break

            if not changes:
                QMessageBox.warning(self, "No iteminfo Patches",
                    "This JSON doesn't contain iteminfo.pabgb patches.\n"
                    f"Found patches for: {', '.join(p.get('game_file','?') for p in patches)}")
                return

            data = bytearray(self._buff_data)
            applied = 0
            skipped = 0
            for change in changes:
                offset = change.get("offset", 0)
                orig_hex = change.get("original", "")
                patch_hex = change.get("patched", "")
                label = change.get("label", "")

                if not orig_hex or not patch_hex:
                    skipped += 1
                    continue

                orig_bytes = bytes.fromhex(orig_hex)
                patch_bytes = bytes.fromhex(patch_hex)

                if len(orig_bytes) != len(patch_bytes):
                    skipped += 1
                    continue

                if offset + len(orig_bytes) > len(data):
                    skipped += 1
                    continue

                actual = bytes(data[offset:offset + len(orig_bytes)])
                if actual == orig_bytes:
                    data[offset:offset + len(patch_bytes)] = patch_bytes
                    applied += 1
                else:
                    skipped += 1

            if applied == 0:
                QMessageBox.warning(self, "No Patches Applied",
                    f"0/{len(changes)} patches matched.\n\n"
                    "This usually means iteminfo.pabgb has been modified by another mod.\n"
                    "Re-extract from vanilla first (click Extract Rust).")
                return

            self._buff_data = data
            self._buff_modified = True

            try:
                import crimson_rs
                rust_items = crimson_rs.parse_iteminfo_from_bytes(bytes(data))
                self._buff_rust_items = rust_items
                self._buff_rust_lookup = {it['key']: it for it in rust_items}
                self._buff_use_rust = True

                self._buff_status_label.setText(
                    f"Imported: {applied}/{len(changes)} patches applied "
                    f"({skipped} skipped). {len(rust_items)} items re-parsed.")

                if hasattr(self, '_buff_table') and self._buff_table.rowCount() > 0:
                    self._buff_search_items()

            except Exception as e:
                self._buff_status_label.setText(
                    f"Patches applied ({applied}) but re-parse failed: {e}")

            mod_name = mod_data.get("name", os.path.basename(path))
            QMessageBox.information(self, "Community Patch Imported",
                f"Imported: {mod_name}\n\n"
                f"Applied: {applied}/{len(changes)} patches\n"
                f"Skipped: {skipped} (offset mismatch or invalid)\n\n"
                f"The changes are now baked into your iteminfo data.\n"
                f"Make any additional ItemBuffs edits, then 'Export as Mod'\n"
                f"to create a combined mod with both changes.")

        except Exception as e:
            import traceback; traceback.print_exc()
            QMessageBox.critical(self, "Import Failed", str(e))

    SE_ITEMBUFFS_DIR = "0058"
    SE_STORES_DIR = "0060"


    def _buff_export_json(self) -> None:
        """Export ItemBuffs changes as a JSON patch file for CD JSON Mod Manager."""
        if not self._buff_ensure_patcher():
            return

        apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
        apply_inf_dura = hasattr(self, '_inf_dura_check') and self._inf_dura_check.isChecked()

        if self._buff_patcher._original_data is None:
            QMessageBox.warning(self, "Export", "Extract iteminfo first.")
            return

        has_cd_patches = bool(getattr(self, '_cd_patches', {}))
        if not self._buff_modified and not apply_stacks and not has_cd_patches and not apply_inf_dura:
            QMessageBox.information(
                self, "No Changes",
                "No modifications have been made. Apply buffs or check 'Max Stacks' first.",
            )
            return

        if self._buff_data is None:
            try:
                raw = self._buff_patcher.extract_iteminfo()
                self._buff_data = bytearray(raw)
                self._buff_items = self._buff_patcher.find_items(bytes(self._buff_data))
            except Exception as e:
                QMessageBox.critical(self, "Extract Failed", str(e))
                return

        if apply_stacks:
            target = self._stack_spin.value()
            if hasattr(self, '_buff_rust_items') and self._buff_rust_items:
                for it in self._buff_rust_items:
                    if it.get('max_stack_count', 1) > 1:
                        it['max_stack_count'] = target

        if apply_inf_dura:
            if hasattr(self, '_buff_rust_items') and self._buff_rust_items:
                dura_count = 0
                for it in self._buff_rust_items:
                    endurance = it.get('max_endurance', 0)
                    if endurance > 0 and endurance != 65535:
                        it['max_endurance'] = 65535
                        it['is_destroy_when_broken'] = 0
                        dura_count += 1
                log.info("JSON Infinity Durability: patched %d items", dura_count)

        original = self._buff_patcher._original_data
        if not self._buff_modified and not apply_stacks and not apply_inf_dura and has_cd_patches:
            final_data = original
        else:
            try:
                import crimson_rs
                if hasattr(self, '_buff_rust_items') and self._buff_rust_items:
                    final_data = crimson_rs.serialize_iteminfo(self._buff_rust_items)
                else:
                    final_data = bytes(self._buff_data) if self._buff_data else original
            except Exception as e:
                QMessageBox.warning(self, "Serialize Failed", str(e))
                return

        if getattr(self, '_vfx_size_changes', None) or getattr(self, '_vfx_swaps', None) \
                or getattr(self, '_vfx_anim_swaps', None) or getattr(self, '_vfx_attach_changes', None) \
                or getattr(self, '_transmog_swaps', None):
            fa = bytearray(final_data)
            self._apply_vfx_changes(fa)
            self._apply_transmog_swaps(fa)
            final_data = bytes(fa)

        if len(final_data) != len(original):
            QMessageBox.warning(self, "Export",
                f"File size changed ({len(original):,} -> {len(final_data):,} bytes).\n"
                "JSON export only supports same-size edits.\n"
                "For structural edits (add buffs/stats), use 'Export as Mod' instead.")
            return

        changes = []
        i = 0
        while i < len(final_data):
            if final_data[i] != original[i]:
                start = i
                while i < len(final_data) and final_data[i] != original[i]:
                    i += 1
                changes.append({
                    "offset": start,
                    "label": f"iteminfo +0x{start:X}",
                    "original": original[start:i].hex(),
                    "patched": final_data[start:i].hex(),
                })
            else:
                i += 1

        for item_key, (abs_off, orig_bytes, new_val) in getattr(self, '_cd_patches', {}).items():
            new_bytes = struct.pack('<I', new_val)
            already = any(c['offset'] == abs_off for c in changes)
            if not already:
                changes.append({
                    "offset": abs_off,
                    "label": f"cooldown item_key={item_key} ({orig_bytes.hex()}->{new_bytes.hex()})",
                    "original": orig_bytes.hex(),
                    "patched": new_bytes.hex(),
                })

        if not changes:
            QMessageBox.information(self, "Export", "No byte-level changes detected.")
            return

        reply = QMessageBox.question(
            self, "Export JSON Patch — Limitations",
            f"JSON Patch exports {len(changes)} byte-level change(s).\n\n"
            f"WHAT THIS SUPPORTS:\n"
            f"  - Change stat values (e.g. DDD 5000 -> 999999)\n"
            f"  - Swap stat hashes within same size class\n"
            f"  - Max stack size changes\n"
            f"  - Cooldown changes (seconds)\n\n"
            f"WHAT THIS DOES NOT SUPPORT:\n"
            f"  - Adding NEW buffs/effects (Fire Res, Ice Res, etc)\n"
            f"  - Adding NEW stats that don't exist on the item\n"
            f"  - Adding passive skills (Invincible, etc)\n"
            f"  - God Mode injection\n"
            f"  - Any edit that changes the file size\n\n"
            f"For those, use 'Export as Mod' instead.\n\n"
            f"Continue with JSON Patch export?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "Export JSON Patch",
                                        "Patch name:", text="My ItemBuffs Mod")
        if not ok or not name.strip():
            return
        name = name.strip()

        path, _ = QFileDialog.getSaveFileName(
            self, "Export JSON Patch", f"{name}.json", "JSON Files (*.json)")
        if not path:
            return

        patch_json = {
            "name": name,
            "version": "1.0",
            "description": f"{len(changes)} iteminfo changes",
            "author": "CrimsonSaveEditor",
            "patches": [{
                "game_file": "gamedata/iteminfo.pabgb",
                "changes": changes,
            }]
        }

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(patch_json, f, indent=2)

        self._buff_status_label.setText(f"Exported {len(changes)} patches to {os.path.basename(path)}")
        QMessageBox.information(self, "Exported",
            f"Saved {len(changes)} patches to:\n{path}\n\n"
            f"Use CD JSON Mod Manager to apply this patch.\n"
            f"Drop the JSON file into the mod manager and click Apply.")


    def _buff_apply_to_game(self) -> None:
        """Apply modified iteminfo.pabgb directly to game via PAZ overlay."""
        if not self._buff_ensure_patcher():
            return

        if not _is_admin():
            QMessageBox.warning(
                self, "Admin Required",
                "Writing to game files in Program Files requires administrator privileges.\n\n"
                "Please restart the editor as Administrator:\n"
                "Right-click → Run as administrator",
            )
            return

        apply_stacks = hasattr(self, '_stack_check') and self._stack_check.isChecked()
        apply_inf_dura = hasattr(self, '_inf_dura_check') and self._inf_dura_check.isChecked()

        if self._buff_data is None:
            if not apply_stacks and not apply_inf_dura:
                QMessageBox.warning(self, "No Data", "Extract iteminfo first.")
                return
            try:
                raw = self._buff_patcher.extract_iteminfo()
                self._buff_data = bytearray(raw)
                self._buff_items = self._buff_patcher.find_items(bytes(self._buff_data))
            except Exception as e:
                QMessageBox.critical(self, "Extract Failed", str(e))
                return

        if not self._buff_modified and not apply_stacks and not apply_inf_dura:
            QMessageBox.information(
                self, "No Changes",
                "No modifications have been made. Apply buffs or check 'Also apply Max Stacks' first.",
            )
            return

        stack_msg = ""
        if apply_stacks:
            target = self._stack_spin.value()
            count, _ = self._buff_patcher.patch_stack_sizes(self._buff_data, target_stack=target)
            stack_msg = f"\nMax Stacks: {count} items set to {target}"

        from pipeline_report import PipelineReport
        rpt = PipelineReport()
        rpt.stage("input", f"flags: stacks={apply_stacks} inf_dura={apply_inf_dura} "
                           f"buff_modified={self._buff_modified} "
                           f"cd_patches={len(getattr(self, '_cd_patches', {}))} "
                           f"transmog_swaps={len(getattr(self, '_transmog_swaps', []) or [])}")

        if hasattr(self, '_buff_rust_items') and self._buff_rust_items:
            try:
                import crimson_rs
                if apply_stacks:
                    target_val = self._stack_spin.value()
                    stack_keys: list[int] = []
                    for it in self._buff_rust_items:
                        if it.get('max_stack_count', 1) > 1:
                            it['max_stack_count'] = target_val
                            stack_keys.append(it.get('key'))
                    rpt.stage("max_stacks", f"set max_stack_count={target_val} on {len(stack_keys)} items")
                    rpt.expect('max_stacks', {'target': target_val, 'items': stack_keys})
                if apply_inf_dura:
                    dura_count = 0
                    dura_keys: list[int] = []
                    for it in self._buff_rust_items:
                        endurance = it.get('max_endurance', 0)
                        if endurance > 0 and endurance != 65535:
                            it['max_endurance'] = 65535
                            it['is_destroy_when_broken'] = 0
                            dura_count += 1
                            dura_keys.append(it.get('key'))
                    log.info("ApplyToGame Infinity Durability: patched %d items", dura_count)
                    rpt.stage("inf_durability", f"set max_endurance=65535 on {dura_count} items")
                    rpt.expect('inf_dura', {'target': 65535, 'items': dura_keys})
                final_data = bytearray(crimson_rs.serialize_iteminfo(self._buff_rust_items))
                rpt.stage("rust_serialize", f"{len(final_data)} bytes")
                if self._apply_vfx_changes(final_data):
                    rpt.stage("vfx_lab", f"applied (blob now {len(final_data)} bytes)")
                cd_patches = getattr(self, '_cd_patches', {})
                cd_expected: dict = {}
                if cd_patches:
                    cd_hit = 0
                    for item_key, (_, _, new_val) in cd_patches.items():
                        cd_off, _ = self._cd_detect(item_key, bytes(final_data))
                        if cd_off is not None:
                            final_data[cd_off:cd_off + 4] = struct.pack('<I', new_val)
                            cd_hit += 1
                            cd_expected[item_key] = (cd_off, new_val)
                    rpt.stage("cooldowns", f"{cd_hit}/{len(cd_patches)} offsets patched")
                    rpt.expect('cooldowns', cd_expected)
                tmog_applied = self._apply_transmog_swaps(final_data)
                if getattr(self, '_transmog_swaps', None):
                    tmog_expected: list[dict] = []
                    from armor_catalog import parse_transmog_items
                    fresh = parse_transmog_items(bytes(final_data))
                    fresh_by_key = {a.item_id: a for a in fresh}
                    for sw in self._transmog_swaps:
                        src_obj = sw.get('src')
                        src_hash = None
                        if hasattr(src_obj, 'hashes') and src_obj.hashes:
                            src_hash = src_obj.hashes[0][1]
                        tgt_key = sw['tgt'].item_id if hasattr(sw.get('tgt'), 'item_id') else sw.get('tgt_key')
                        fresh_tgt = fresh_by_key.get(tgt_key)
                        offsets = [o for o, _ in (fresh_tgt.hashes if fresh_tgt else [])]
                        tmog_expected.append({
                            'tgt_key': tgt_key,
                            'src_hash': src_hash,
                            'offsets': offsets[:1],
                        })
                    rpt.stage("transmog", f"{tmog_applied} byte patches for {len(self._transmog_swaps)} swap(s)")
                    rpt.expect('transmog', tmog_expected)
                rpt.verify(bytes(final_data))
                rpt.write()
                final_data = bytes(final_data)
            except Exception as e:
                log.warning("Rust serialize failed, using byte buffer: %s", e)
                rpt.stage("rust_serialize", f"FAILED: {e}")
                rpt.write()
                final_data = bytes(self._buff_data)
        else:
            final_data = bytes(self._buff_data)
            rpt.stage("rust_serialize", "SKIPPED (no rust items) — using byte buffer")
            rpt.write()

        game_path = self._buff_patcher.game_path

        changes = []
        if self._buff_modified:
            changes.append("stat buffs")
        if apply_stacks:
            changes.append(f"max stacks")

        buff_dir = f"{self._buff_overlay_spin.value():04d}"
        reply = QMessageBox.question(
            self, "Export JSON Patch",
            f"Pack modified iteminfo.pabgb into {buff_dir}/ override directory?\n\n"
            f"Changes: {' + '.join(changes)}\n"
            f"Data: {len(final_data):,} bytes\n\n"
            f"Uses Potter's pack_mod pipeline (same as community tools).\n"
            f"Original 0008/0.paz is NOT modified.\n"
            f"To undo: click Restore Original.\n\n"
            f"The game must be restarted for changes to take effect.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._buff_status_label.setText("Packing with pack_mod...")
        QApplication.processEvents()

        try:
            import crimson_rs.pack_mod
            import shutil
            import tempfile

            with tempfile.TemporaryDirectory() as tmp_dir:
                mod_dir = os.path.join(tmp_dir, "gamedata", "binary__", "client", "bin")
                os.makedirs(mod_dir, exist_ok=True)
                with open(os.path.join(mod_dir, "iteminfo.pabgb"), "wb") as f:
                    f.write(final_data)

                out_dir = os.path.join(tmp_dir, "output")
                os.makedirs(out_dir, exist_ok=True)

                crimson_rs.pack_mod.pack_mod(
                    game_dir=game_path,
                    mod_folder=tmp_dir,
                    output_dir=out_dir,
                    group_name=buff_dir,
                )

                papgt_path = os.path.join(game_path, "meta", "0.papgt")
                papgt_backup = papgt_path + ".sebak"
                papgt_vanilla = papgt_path + ".vanilla"
                if os.path.isfile(papgt_path):
                    try:
                        import crimson_rs as _cr
                        cur = _cr.parse_papgt_file(papgt_path)
                        has_our_entry = any(
                            e['group_name'] == buff_dir for e in cur['entries']
                        )
                    except Exception:
                        has_our_entry = False
                    if not has_our_entry:
                        shutil.copy2(papgt_path, papgt_backup)
                    if not os.path.isfile(papgt_vanilla):
                        shutil.copy2(papgt_path, papgt_vanilla)

                game_mod = os.path.join(game_path, buff_dir)
                if os.path.isdir(game_mod):
                    shutil.rmtree(game_mod)
                os.makedirs(game_mod, exist_ok=True)

                shutil.copy2(
                    os.path.join(out_dir, buff_dir, "0.paz"),
                    os.path.join(game_mod, "0.paz"),
                )
                shutil.copy2(
                    os.path.join(out_dir, buff_dir, "0.pamt"),
                    os.path.join(game_mod, "0.pamt"),
                )
                shutil.copy2(
                    os.path.join(out_dir, "meta", "0.papgt"),
                    papgt_path,
                )

                with open(os.path.join(game_mod, ".se_itembuffs"), "w") as mf:
                    mf.write("Created by CrimsonSaveEditor ItemBuffs tab\n")

            paz_size = os.path.getsize(os.path.join(game_mod, "0.paz"))
            msg = (
                f"Packed to {buff_dir}/ via pack_mod ({paz_size:,} bytes)\n"
                f"PAPGT replaced with generated version\n"
                f"Original 0008/0.paz untouched"
            )
            msg += stack_msg

            self._buff_modified = False
            self._buff_status_label.setText(f"Success: packed to {buff_dir}/")
            QMessageBox.information(self, "Applied Successfully", msg)
            self.paz_refresh_requested.emit()

        except Exception as e:
            import traceback; traceback.print_exc()
            self._buff_status_label.setText(f"Failed: {e}")
            QMessageBox.critical(self, "Apply Failed", str(e))


    def _rebuild_papgt_without(self, game_path: str, group_to_remove: str) -> str:
        """Rebuild PAPGT by removing a specific group entry.

        Preserves all other overlay entries (e.g. removing 0058 keeps 0060).
        Returns a status message.
        """
        try:
            import crimson_rs
            papgt_path = os.path.join(game_path, "meta", "0.papgt")
            if not os.path.isfile(papgt_path):
                return "PAPGT not found"

            papgt = crimson_rs.parse_papgt_file(papgt_path)
            original_count = len(papgt['entries'])
            papgt['entries'] = [
                e for e in papgt['entries']
                if e['group_name'] != group_to_remove
            ]
            new_count = len(papgt['entries'])

            if new_count == original_count:
                return f"PAPGT: {group_to_remove} was not registered"

            crimson_rs.write_papgt_file(papgt, papgt_path)
            remaining = [e['group_name'] for e in papgt['entries'] if int(e['group_name']) >= 36]
            extra = f" (other overlays still active: {', '.join(remaining)})" if remaining else ""
            return f"PAPGT: removed {group_to_remove} entry{extra}"
        except Exception as e:
            sebak = os.path.join(game_path, "meta", "0.papgt.sebak")
            if os.path.isfile(sebak):
                import shutil
                shutil.copy2(sebak, papgt_path)
                return f"PAPGT: fell back to .sebak restore ({e})"
            return f"PAPGT rebuild failed: {e}"


    def _buff_reset_vanilla_papgt(self) -> None:
        """Emergency: copy .papgt.vanilla over the live PAPGT.

        Disables every overlay registered in PAPGT — the user will need to
        re-apply any mods (buffs, stores, fields, etc) afterward. Intended
        for 'the game won't launch' recovery scenarios.
        """
        if not self._buff_ensure_patcher():
            return
        if not _is_admin():
            QMessageBox.warning(
                self, "Admin Required",
                "Resetting PAPGT requires administrator privileges.\n\n"
                "Right-click → Run as administrator",
            )
            return

        game_path = self._buff_patcher.game_path
        papgt_path = os.path.join(game_path, "meta", "0.papgt")
        vanilla = papgt_path + ".vanilla"
        sebak = papgt_path + ".sebak"

        source = vanilla if os.path.isfile(vanilla) else (sebak if os.path.isfile(sebak) else None)
        if source is None:
            QMessageBox.warning(
                self, "No Backup",
                "Neither .papgt.vanilla nor .papgt.sebak exists.\n"
                "This button only works after at least one Apply to Game.\n\n"
                "Use Steam > Verify Integrity of Game Files instead.",
            )
            return

        src_label = os.path.basename(source)
        reply = QMessageBox.question(
            self, "Reset to Vanilla PAPGT",
            f"Copy {src_label} over meta/0.papgt?\n\n"
            f"This disables ALL registered overlays. Any mods (buffs, stores,\n"
            f"fields, etc) will need to be re-applied afterward.\n\n"
            f"Use only if the game won't launch. Proceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            import shutil
            shutil.copy2(source, papgt_path)
            QMessageBox.information(
                self, "Reset Done",
                f"Copied {src_label} over meta/0.papgt.\n"
                f"Restart the game to verify it launches cleanly.",
            )
            self._buff_status_label.setText(f"PAPGT reset from {src_label}")
            self.paz_refresh_requested.emit()
        except Exception as e:
            QMessageBox.critical(self, "Reset Failed", str(e))


    def _buff_restore_original(self) -> None:
        """Remove ItemBuffs overlay and its PAPGT entry, preserving other overlays."""
        if not self._buff_ensure_patcher():
            return

        if not _is_admin():
            QMessageBox.warning(
                self, "Admin Required",
                "Restoring game files requires administrator privileges.\n\n"
                "Right-click → Run as administrator",
            )
            return

        game_path = self._buff_patcher.game_path
        import shutil

        buff_dir = f"{self._buff_overlay_spin.value():04d}"
        game_mod = os.path.join(game_path, buff_dir)
        has_mod_dir = os.path.isdir(game_mod)

        legacy_mod = os.path.join(game_path, "0038")
        has_legacy = os.path.isdir(legacy_mod) and not has_mod_dir

        paz_backup = self._buff_patcher.paz_path + ".backup"
        has_inplace_backup = os.path.isfile(paz_backup)

        if not has_mod_dir and not has_legacy and not has_inplace_backup:
            QMessageBox.information(
                self, "Nothing to Restore",
                "No backups or mod directories found.\n"
                "ItemBuffs may not have been applied yet.",
            )
            return

        actual_dir = buff_dir if has_mod_dir else "0038"
        parts = []
        if has_mod_dir or has_legacy:
            parts.append(f"Delete {actual_dir}/ override directory")
            parts.append(f"Remove {actual_dir} from PAPGT (preserves other overlays)")
        if has_inplace_backup:
            parts.append("Restore 0008/0.paz from .backup files")

        reply = QMessageBox.question(
            self, "Restore Original",
            "\n".join(parts) + "\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        self._buff_status_label.setText("Restoring...")
        QApplication.processEvents()

        messages = []
        papgt_path = os.path.join(game_path, "meta", "0.papgt")
        sebak = papgt_path + ".sebak"
        vanilla = papgt_path + ".vanilla"

        target_dir = game_mod if has_mod_dir else legacy_mod
        if has_mod_dir or has_legacy:
            restored = False
            rebuild_msg = self._rebuild_papgt_without(game_path, actual_dir)
            if rebuild_msg and 'failed' not in rebuild_msg.lower():
                messages.append(f"PAPGT: {rebuild_msg} (surgical remove-only)")
                restored = True
            else:
                messages.append(f"PAPGT rebuild: {rebuild_msg}")
            if not restored and os.path.isfile(sebak):
                try:
                    shutil.copy2(sebak, papgt_path)
                    messages.append("PAPGT: fell back to .sebak byte-copy")
                    restored = True
                except Exception as e:
                    messages.append(f"PAPGT .sebak restore failed: {e}")
            if not restored and os.path.isfile(vanilla):
                try:
                    shutil.copy2(vanilla, papgt_path)
                    messages.append("PAPGT: fell back to .vanilla byte-copy "
                                    "(nuclear — other overlays disabled)")
                    restored = True
                except Exception as e:
                    messages.append(f"PAPGT .vanilla restore failed: {e}")

        if has_mod_dir or has_legacy:
            try:
                shutil.rmtree(target_dir)
                messages.append(f"Removed {actual_dir}/ override directory")
            except Exception as e:
                messages.append(f"Failed to remove {actual_dir}/: {e}")


        if has_inplace_backup:
            ok, msg = self._paz_manager.restore_all_backups()
            messages.append(msg)

        self._buff_data = None
        self._buff_modified = False
        self._buff_items = []
        self._buff_current_item = None
        self._buff_items_table.setRowCount(0)
        self._buff_stats_table.setRowCount(0)

        full_msg = "\n".join(messages)
        self._buff_status_label.setText("Restored successfully")
        QMessageBox.information(self, "Restored", full_msg)
        self.paz_refresh_requested.emit()


    def _set_refresh_local(self) -> None:
        """Refresh the sets table from local files."""
        sets = self._set_mgr.scan_local()
        table = self._set_table
        table.setRowCount(len(sets))
        for row, es in enumerate(sets):
            table.setItem(row, 0, QTableWidgetItem(es.name))
            table.setItem(row, 1, QTableWidgetItem(es.author))
            table.setItem(row, 2, QTableWidgetItem(str(len(es.items))))
            table.setItem(row, 3, QTableWidgetItem(es.description))
        self._set_status.setText(f"{len(sets)} local sets")


    def _set_get_selected(self) -> Optional[EquipmentSet]:
        rows = self._set_table.selectionModel().selectedRows()
        if not rows:
            return None
        idx = rows[0].row()
        sets = self._set_mgr.scan_local()
        return sets[idx] if idx < len(sets) else None


    def _buff_items_context_menu(self, pos) -> None:
        """Right-click on buff items table — add to equipment set."""
        item_widget = self._buff_items_table.itemAt(pos)
        if not item_widget:
            return
        row = item_widget.row()
        name_cell = self._buff_items_table.item(row, 1)
        if not name_cell:
            return
        item = name_cell.data(Qt.UserRole)
        if not item:
            return

        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        add_action = menu.addAction("Add to Equipment Set...")
        action = menu.exec(self._buff_items_table.viewport().mapToGlobal(pos))
        if action == add_action:
            self._set_add_item(item)


    def _buff_build_operations(self, item) -> List[StatOperation]:
        """Build stat operations from the current preset selection for an item.
        Returns list of StatOperation without applying anything."""
        from paz_patcher import _stat_size_class, BUFF_NAMES, BUFF_HASHES

        if self._buff_data is None:
            return []

        arrays = ItemBuffPatcher.find_stat_arrays(bytes(self._buff_data), item)
        if not arrays:
            return []

        all_entries = []
        for arr in arrays:
            all_entries.extend(arr.entries)

        preset_idx = self._buff_preset_combo.currentIndex()
        ops = []

        if preset_idx == 0:
            for entry in all_entries:
                val = 15 if entry.size_class == "rate" else 999_999
                ops.append(StatOperation(
                    stat_name=entry.name, stat_hash=entry.hash_val,
                    size_class=entry.size_class, operation="set_value", value=val,
                ))
        elif preset_idx in (1, 2, 3, 4, 5):
            target_classes = {
                1: ("flat2", "flat1"), 2: ("flat2",), 3: ("flat2",),
                4: ("flat1",), 5: ("rate",),
            }[preset_idx]
            for entry in all_entries:
                if entry.size_class in target_classes:
                    val = 15 if entry.size_class == "rate" else 999_999
                    ops.append(StatOperation(
                        stat_name=entry.name, stat_hash=entry.hash_val,
                        size_class=entry.size_class, operation="set_value", value=val,
                    ))
        elif preset_idx in (6, 7):
            target = BUFF_HASHES["Damage Dealt (DDD)"] if preset_idx == 6 else BUFF_HASHES["Defense (DPV)"]
            target_name = "DDD (Damage)" if preset_idx == 6 else "DPV (Defense)"
            for entry in all_entries:
                if entry.size_class == "flat2" and entry.hash_val != target:
                    ops.append(StatOperation(
                        stat_name=entry.name, stat_hash=entry.hash_val,
                        size_class="flat2", operation="swap_hash",
                        value=entry.value, target_hash=target,
                    ))
        else:
            buff_name = self._buff_type_combo.currentText()
            buff_hash = BUFF_HASHES.get(buff_name)
            if buff_hash is None:
                return []
            value = self._buff_value_spin.value()
            target_class = _stat_size_class(buff_hash)
            for entry in all_entries:
                if entry.size_class == target_class:
                    if entry.hash_val == buff_hash:
                        ops.append(StatOperation(
                            stat_name=entry.name, stat_hash=entry.hash_val,
                            size_class=target_class, operation="set_value", value=value,
                        ))
                    else:
                        ops.append(StatOperation(
                            stat_name=entry.name, stat_hash=entry.hash_val,
                            size_class=target_class, operation="swap_hash",
                            value=value, target_hash=buff_hash,
                        ))
        return ops


    def _set_add_item(self, item) -> None:
        """Add an item with current preset operations to a set."""
        ops = self._buff_build_operations(item)
        if not ops:
            QMessageBox.information(self, "No Operations",
                                    "Select a preset first, then right-click the item.")
            return

        display_name = self._name_db.get_name(item.item_key)
        if display_name.startswith("Unknown"):
            display_name = item.name

        sets = self._set_mgr.scan_local()
        choices = [es.name for es in sets] + ["-- Create New Set --"]

        from PySide6.QtWidgets import QInputDialog
        choice, ok = QInputDialog.getItem(
            self, "Add to Equipment Set",
            f"Add '{display_name}' with {len(ops)} operations to:",
            choices, 0, False,
        )
        if not ok:
            return

        if choice == "-- Create New Set --":
            name, ok2 = QInputDialog.getText(self, "New Set", "Set name:")
            if not ok2 or not name.strip():
                return
            author, ok3 = QInputDialog.getText(self, "New Set", "Author:")
            if not ok3:
                author = ""
            desc, ok4 = QInputDialog.getText(self, "New Set", "Description:")
            if not ok4:
                desc = ""
            import datetime
            es = EquipmentSet(
                name=name.strip(), author=author.strip(),
                description=desc.strip(),
                created=datetime.date.today().isoformat(),
            )
        else:
            es = next((s for s in sets if s.name == choice), None)
            if not es:
                return

        set_item = SetItem(
            item_key=item.item_key,
            item_name=display_name,
            operations=ops,
        )

        es.items = [si for si in es.items if si.item_key != item.item_key]
        es.items.append(set_item)

        self._set_mgr.save_set(es, es.filename if es.filename else "")
        self._set_refresh_local()
        self._set_status.setText(f"Added {display_name} to '{es.name}' ({len(ops)} ops)")


    def _set_apply(self) -> None:
        """Apply the selected equipment set to iteminfo in memory."""
        es = self._set_get_selected()
        if not es:
            QMessageBox.information(self, "No Set", "Select an equipment set first.")
            return

        if self._buff_data is None:
            QMessageBox.warning(self, "No Data", "Extract iteminfo first (use Extract button above).")
            return

        if not self._buff_items:
            QMessageBox.warning(self, "No Items", "Extract and search for items first.")
            return

        item_by_key = {}
        for it in self._buff_items:
            item_by_key[it.item_key] = it

        lines = []
        for si in es.items:
            found = "YES" if si.item_key in item_by_key else "NO"
            lines.append(f"  {si.item_name} (key={si.item_key}): {len(si.operations)} ops [{found}]")

        reply = QMessageBox.question(
            self, f"Apply Set: {es.name}",
            f"Apply '{es.name}' by {es.author}?\n\n"
            f"{len(es.items)} items:\n" + "\n".join(lines) + "\n\n"
            f"Changes held in memory until 'Export JSON Patch'.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        applied = 0
        skipped = 0
        total_ops = 0

        for si in es.items:
            item_rec = item_by_key.get(si.item_key)
            if not item_rec:
                skipped += 1
                continue

            arrays = ItemBuffPatcher.find_stat_arrays(bytes(self._buff_data), item_rec)
            all_entries = []
            for arr in arrays:
                all_entries.extend(arr.entries)

            if not all_entries:
                skipped += 1
                continue

            entries_by_class = {}
            for e in all_entries:
                entries_by_class.setdefault(e.size_class, []).append(e)

            ops_by_class = {}
            for op in si.operations:
                ops_by_class.setdefault(op.size_class, []).append(op)

            for cls, ops in ops_by_class.items():
                entries = entries_by_class.get(cls, [])
                for i, op in enumerate(ops):
                    if i >= len(entries):
                        break
                    entry = entries[i]
                    if op.operation == "set_value":
                        ItemBuffPatcher.overwrite_stat_value(self._buff_data, entry, op.value)
                        total_ops += 1
                    elif op.operation == "swap_hash":
                        if ItemBuffPatcher.swap_stat_hash(self._buff_data, entry, op.target_hash):
                            ItemBuffPatcher.overwrite_stat_value(self._buff_data, entry, op.value)
                            total_ops += 1

            applied += 1

        self._buff_modified = True
        if self._buff_current_item:
            self._buff_refresh_stats()

        msg = f"Applied '{es.name}': {applied}/{len(es.items)} items, {total_ops} operations"
        if skipped:
            msg += f" ({skipped} items not found in iteminfo)"
        self._set_status.setText(msg)
        self._buff_status_label.setText(msg + ". Click 'Export JSON Patch' to write.")


    def _set_preview(self) -> None:
        """Preview the contents of the selected set."""
        es = self._set_get_selected()
        if not es:
            QMessageBox.information(self, "No Set", "Select a set first.")
            return

        from paz_patcher import BUFF_NAMES
        lines = [f"Set: {es.name}\nAuthor: {es.author}\n{es.description}\n"]
        for si in es.items:
            lines.append(f"\n{si.item_name} (key={si.item_key}):")
            for op in si.operations:
                if op.operation == "set_value":
                    val_str = f"Lv {op.value}" if op.size_class == "rate" else f"{op.value:,}"
                    lines.append(f"  {op.stat_name} = {val_str}")
                elif op.operation == "swap_hash":
                    target_name = BUFF_NAMES.get(op.target_hash, f"0x{op.target_hash:08X}")
                    lines.append(f"  {op.stat_name} -> {target_name} = {op.value:,}")

        QMessageBox.information(self, f"Set Preview: {es.name}", "\n".join(lines))


    def _set_create_new(self) -> None:
        """Create a new empty equipment set."""
        from PySide6.QtWidgets import QInputDialog
        import datetime
        name, ok = QInputDialog.getText(self, "New Equipment Set", "Set name:")
        if not ok or not name.strip():
            return
        author, ok2 = QInputDialog.getText(self, "New Equipment Set", "Author:")
        if not ok2:
            author = ""
        desc, ok3 = QInputDialog.getText(self, "New Equipment Set", "Description:")
        if not ok3:
            desc = ""
        es = EquipmentSet(
            name=name.strip(), author=author.strip(),
            description=desc.strip(),
            created=datetime.date.today().isoformat(),
        )
        self._set_mgr.save_set(es)
        self._set_refresh_local()
        self._set_status.setText(f"Created set '{es.name}'")


    def _set_delete(self) -> None:
        """Delete the selected set."""
        es = self._set_get_selected()
        if not es:
            return
        reply = QMessageBox.question(
            self, "Delete Set", f"Delete '{es.name}'?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._set_mgr.delete_set(es.filename)
            self._set_refresh_local()


    def _set_import(self) -> None:
        """Import a set from a JSON file."""
        path, _ = QFileDialog.getOpenFileName(self, "Import Equipment Set", "", "JSON (*.json)")
        if not path:
            return
        es = self._set_mgr.load_set_file(path)
        if es:
            self._set_mgr.save_set(es)
            self._set_refresh_local()
            self._set_status.setText(f"Imported '{es.name}'")
        else:
            QMessageBox.warning(self, "Import Failed", "Could not parse set file.")


    def _set_export(self) -> None:
        """Export the selected set to a JSON file."""
        es = self._set_get_selected()
        if not es:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Equipment Set", es.filename or f"{es.name}.json", "JSON (*.json)",
        )
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._set_mgr.export_set_json(es))
            self._set_status.setText(f"Exported '{es.name}' to {os.path.basename(path)}")


    def _set_refresh_github(self) -> None:
        """Fetch community sets from GitHub and download new ones."""
        self._set_status.setText("Fetching community sets...")
        QApplication.processEvents()
        ok, msg = self._set_mgr.fetch_remote_index()
        if not ok:
            self._set_status.setText(msg)
            return
        remote = self._set_mgr.get_remote_index()
        downloaded = 0
        for entry in remote:
            local_path = os.path.join(self._set_mgr.local_dir, entry.filename)
            if not os.path.isfile(local_path):
                es, dl_msg = self._set_mgr.download_set(entry.filename)
                if es:
                    downloaded += 1
        self._set_refresh_local()
        self._set_status.setText(f"{msg} Downloaded {downloaded} new sets.")
