"""Shared UI helper functions used by multiple tabs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QTableWidgetItem
from gui.theme import COLORS


def make_scope_label(scope: str) -> QLabel:
    """Create a scope indicator label showing what this tab modifies."""
    if scope == "save":
        text = "This tab modifies your SAVE FILE"
        color = COLORS["scope_save"]
        bg = "rgba(79,195,247,0.08)"
    elif scope == "game":
        text = "This tab modifies GAME FILES (requires admin + restart)"
        color = COLORS["scope_game"]
        bg = "rgba(255,183,77,0.08)"
    elif scope == "readonly":
        text = "This tab is READ-ONLY (browse only)"
        color = COLORS["text_dim"]
        bg = "rgba(176,160,136,0.05)"
    else:
        raise ValueError(f"Unknown scope {scope!r} — expected 'save', 'game', or 'readonly'")
    lbl = QLabel(text)
    lbl.setStyleSheet(
        f"color: {color}; font-size: 11px; padding: 3px 8px; "
        f"border: 1px solid {color}; border-radius: 3px; "
        f"background-color: {bg}; font-weight: bold;"
    )
    lbl.setFixedHeight(22)
    return lbl


def _num_item(value: int) -> QTableWidgetItem:
    """Create a QTableWidgetItem that sorts numerically, not alphabetically."""
    item = QTableWidgetItem()
    item.setData(Qt.DisplayRole, value)
    return item


def make_help_btn(guide_key: str, show_guide_fn) -> QPushButton:
    """Create a small '?' help button that opens the guide for a tab."""
    btn = QPushButton("?")
    btn.setFixedSize(28, 28)
    btn.setToolTip("Show help for this tab")
    btn.setStyleSheet(
        f"QPushButton {{ background-color: {COLORS['error']}; color: white; "
        f"font-weight: bold; font-size: 14px; border: 2px solid {COLORS['error']}; "
        f"border-radius: 14px; padding: 0; }}"
        f"QPushButton:hover {{ background-color: #ff6655; border-color: #ff6655; }}"
    )
    btn.clicked.connect(lambda: show_guide_fn(guide_key))
    return btn
