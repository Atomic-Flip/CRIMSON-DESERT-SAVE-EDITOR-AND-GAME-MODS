"""Theme constants and application stylesheet for the dark UI."""
from __future__ import annotations
import base64 as _b64

COLORS = {
    "bg": "#1a1510",
    "panel": "#272018",
    "header": "#3d2e1a",
    "accent": "#daa850",
    "text": "#f0e6d4",
    "text_dim": "#b0a088",
    "selected": "#5c4320",
    "border": "#554430",
    "input_bg": "#1e1610",
    "success": "#9cc470",
    "warning": "#f0b040",
    "error": "#d44f40",
    "scope_save": "#4FC3F7",
    "scope_game": "#FFB74D",
}

CATEGORY_COLORS = {
    "Equipment": "#d4a24e",
    "Material": "#c9b458",
    "Quest": "#e8a838",
    "Currency": "#dbb742",
    "Consumable": "#8cb369",
    "Ammo": "#c44536",
    "Misc": "#998b72",
}

_TAB_SELECTED_BG = "#2a3040"
_TAB_SELECTED_COLOR = "#e0eaff"
_TAB_SELECTED_BORDER = "#70a8ff"

_COMBO_ARROW_URI = "data:image/svg+xml;base64," + _b64.b64encode(
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6">'
    b'<polygon points="0,0 10,0 5,6" fill="#f0e6d4"/>'
    b'</svg>'
).decode()

DARK_STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {COLORS['bg']};
    color: {COLORS['text']};
    font-family: Consolas, 'Courier New', monospace;
    font-size: 13px;
}}
QMenuBar {{
    background-color: {COLORS['header']};
    color: {COLORS['text']};
    border-bottom: 1px solid {COLORS['border']};
    padding: 2px;
}}
QMenuBar::item:selected {{
    background-color: {COLORS['selected']};
}}
QMenu {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
}}
QMenu::item:selected {{
    background-color: {COLORS['selected']};
}}
QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    background-color: {COLORS['bg']};
}}
QTabBar::tab {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    padding: 8px 18px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    border: 1px solid {COLORS['border']};
    border-bottom: none;
}}
QTabBar::tab:selected {{
    background-color: {_TAB_SELECTED_BG};
    color: {_TAB_SELECTED_COLOR};
    border-bottom: 3px solid {_TAB_SELECTED_BORDER};
    font-weight: bold;
}}
QTabBar::tab:hover {{
    background-color: {COLORS['selected']};
}}
QTableWidget {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    gridline-color: {COLORS['border']};
    selection-background-color: {COLORS['selected']};
    selection-color: white;
    border: 1px solid {COLORS['border']};
    font-family: Consolas, monospace;
    font-size: 12px;
}}
QTableWidget::item {{
    padding: 3px 6px;
}}
QHeaderView::section {{
    background-color: {COLORS['header']};
    color: {COLORS['text']};
    padding: 5px 8px;
    border: 1px solid {COLORS['border']};
    font-weight: bold;
}}
QPushButton {{
    background-color: {COLORS['header']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    padding: 6px 16px;
    border-radius: 3px;
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {COLORS['selected']};
    border-color: {COLORS['accent']};
}}
QPushButton:pressed {{
    background-color: {COLORS['accent']};
}}
QPushButton#accentBtn {{
    background-color: {COLORS['accent']};
    color: white;
}}
QPushButton#accentBtn:hover {{
    background-color: #e8b85e;
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {COLORS['input_bg']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    padding: 5px 8px;
    border-radius: 3px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {COLORS['accent']};
}}
QComboBox::drop-down {{
    border: none;
    border-left: 1px solid {COLORS['border']};
    background-color: {COLORS['header']};
    width: 24px;
}}
QComboBox::down-arrow {{
    image: url("{_COMBO_ARROW_URI}");
    width: 10px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    selection-background-color: {COLORS['selected']};
    border: 1px solid {COLORS['border']};
}}
QGroupBox {{
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 14px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}}
QStatusBar {{
    background-color: {COLORS['header']};
    color: {COLORS['text']};
    border-top: 1px solid {COLORS['border']};
}}
QListWidget {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['selected']};
}}
QTextEdit {{
    background-color: {COLORS['panel']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
}}
QScrollBar:vertical {{
    background-color: {COLORS['bg']};
    width: 12px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {COLORS['border']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background-color: {COLORS['bg']};
    height: 12px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {COLORS['border']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QCheckBox {{
    color: {COLORS['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
}}

/* ── Resize handles: visible bars so users know what's draggable ── */
QSplitter::handle {{
    background-color: {COLORS['border']};
    border: 1px solid {COLORS['accent']};
}}
QSplitter::handle:horizontal {{
    width: 6px;
    margin: 2px 1px;
    border-radius: 2px;
}}
QSplitter::handle:vertical {{
    height: 6px;
    margin: 1px 2px;
    border-radius: 2px;
}}
QSplitter::handle:hover {{
    background-color: {COLORS['accent']};
    border-color: {COLORS['text']};
}}
QSplitter::handle:pressed {{
    background-color: {COLORS['scope_save']};
}}

/* Dock separators (between dock widgets and the central widget) */
QMainWindow::separator {{
    background-color: {COLORS['border']};
    width: 5px;
    height: 5px;
}}
QMainWindow::separator:hover {{
    background-color: {COLORS['accent']};
}}

QDockWidget {{
    border: 1px solid {COLORS['border']};
}}
QDockWidget::title {{
    background: {COLORS['header']};
    color: {COLORS['text']};
    padding: 4px 8px;
    border-bottom: 2px solid {COLORS['accent']};
}}
"""
