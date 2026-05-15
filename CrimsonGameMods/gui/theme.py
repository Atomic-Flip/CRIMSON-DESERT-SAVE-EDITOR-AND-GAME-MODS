from __future__ import annotations
import base64 as _b64

DARK_COLORS = {
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

LIGHT_COLORS = {
    "bg": "#f5f5f5",
    "panel": "#ffffff",
    "header": "#d8d8d8",
    "accent": "#8B3A00",        # darker orange so it's readable on white
    "text": "#111111",
    "text_dim": "#3d3d3d",       # much darker than before (was #555555)
    "selected": "#b8d9ff",
    "border": "#a0a0a0",
    "input_bg": "#ffffff",
    "success": "#1b5e20",
    "warning": "#bf5500",
    "error": "#b71c1c",
    "scope_save": "#01579B",
    "scope_game": "#BF360C",
}

# Active palette — mutated by apply_theme() so existing `COLORS[...]` reads
# during runtime pick up the new values after a switch. Widgets that inline-
# style via COLORS['x'] update on next repaint; widgets baked into the
# compiled stylesheet update immediately via setStyleSheet().
COLORS = dict(DARK_COLORS)

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

_TAB_SELECTED_BG_LIGHT = "#cfe3ff"
_TAB_SELECTED_COLOR_LIGHT = "#103060"
_TAB_SELECTED_BORDER_LIGHT = "#2277dd"


def _combo_arrow_uri(fill: str) -> str:
    return "data:image/svg+xml;base64," + _b64.b64encode(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="6">'
        f'<polygon points="0,0 10,0 5,6" fill="{fill}"/>'
        f'</svg>'.encode()
    ).decode()


_COMBO_ARROW_URI = _combo_arrow_uri("#f0e6d4")


def build_stylesheet(c: dict, tab_bg: str, tab_color: str, tab_border: str, arrow_fill: str) -> str:
    arrow = _combo_arrow_uri(arrow_fill)
    return f"""
QMainWindow, QWidget {{
    background-color: {c['bg']};
    color: {c['text']};
    font-family: Consolas, 'Courier New', monospace;
    font-size: 13px;
}}
QMenuBar {{
    background-color: {c['header']};
    color: {c['text']};
    border-bottom: 1px solid {c['border']};
    padding: 2px;
}}
QMenuBar::item:selected {{
    background-color: {c['selected']};
}}
QMenu {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
}}
QMenu::item:selected {{
    background-color: {c['selected']};
}}
QTabWidget::pane {{
    border: 1px solid {c['border']};
    background-color: {c['bg']};
}}
QTabBar::tab {{
    background-color: {c['panel']};
    color: {c['text']};
    padding: 8px 18px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    border: 1px solid {c['border']};
    border-bottom: none;
}}
QTabBar::tab:selected {{
    background-color: {tab_bg};
    color: {tab_color};
    border-bottom: 3px solid {tab_border};
    font-weight: bold;
}}
QTabBar::tab:hover {{
    background-color: {c['selected']};
}}
QTableWidget {{
    background-color: {c['panel']};
    color: {c['text']};
    gridline-color: {c['border']};
    selection-background-color: {c['selected']};
    selection-color: white;
    border: 1px solid {c['border']};
    font-family: Consolas, monospace;
    font-size: 12px;
}}
QTableWidget::item {{
    padding: 3px 6px;
}}
QHeaderView::section {{
    background-color: {c['header']};
    color: {c['text']};
    padding: 5px 8px;
    border: 1px solid {c['border']};
    font-weight: bold;
}}
QPushButton {{
    background-color: {c['header']};
    color: {c['text']};
    border: 1px solid {c['border']};
    padding: 6px 16px;
    border-radius: 3px;
    font-weight: bold;
}}
QPushButton:hover {{
    background-color: {c['selected']};
    border-color: {c['accent']};
}}
QPushButton:pressed {{
    background-color: {c['accent']};
}}
QPushButton#accentBtn {{
    background-color: {c['accent']};
    color: white;
}}
QPushButton#accentBtn:hover {{
    background-color: #e8b85e;
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {c['input_bg']};
    color: {c['text']};
    border: 1px solid {c['border']};
    padding: 5px 8px;
    border-radius: 3px;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border-color: {c['accent']};
}}
QComboBox::drop-down {{
    border: none;
    border-left: 1px solid {c['border']};
    background-color: {c['header']};
    width: 24px;
}}
QComboBox::down-arrow {{
    image: url("{arrow}");
    width: 10px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['panel']};
    color: {c['text']};
    selection-background-color: {c['selected']};
    border: 1px solid {c['border']};
}}
QGroupBox {{
    color: {c['text']};
    border: 1px solid {c['border']};
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
    background-color: {c['header']};
    color: {c['text']};
    border-top: 1px solid {c['border']};
}}
QListWidget {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
    selection-background-color: {c['selected']};
}}
QTextEdit {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
}}
QScrollBar:vertical {{
    background-color: {c['bg']};
    width: 12px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background-color: {c['border']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background-color: {c['bg']};
    height: 12px;
    border: none;
}}
QScrollBar::handle:horizontal {{
    background-color: {c['border']};
    border-radius: 4px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QCheckBox {{
    color: {c['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
}}

/* ── Resize handles: visible bars so users know what's draggable ── */
QSplitter::handle {{
    background-color: {c['border']};
    border: 1px solid {c['accent']};
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
    background-color: {c['accent']};
    border-color: {c['text']};
}}
QSplitter::handle:pressed {{
    background-color: {c['scope_save']};
}}

/* Dock separators (between dock widgets and the central widget) */
QMainWindow::separator {{
    background-color: {c['border']};
    width: 5px;
    height: 5px;
}}
QMainWindow::separator:hover {{
    background-color: {c['accent']};
}}

QDockWidget {{
    border: 1px solid {c['border']};
}}
QDockWidget::title {{
    background: {c['header']};
    color: {c['text']};
    padding: 4px 8px;
    border-bottom: 2px solid {c['accent']};
}}
"""

DARK_STYLESHEET = build_stylesheet(
    DARK_COLORS, _TAB_SELECTED_BG, _TAB_SELECTED_COLOR,
    _TAB_SELECTED_BORDER, '#f0e6d4')

LIGHT_STYLESHEET = build_stylesheet(
    LIGHT_COLORS, _TAB_SELECTED_BG_LIGHT, _TAB_SELECTED_COLOR_LIGHT,
    _TAB_SELECTED_BORDER_LIGHT, '#333333')


def apply_theme(app, mode: str) -> str:
    """Apply dark or light theme to the QApplication. Also mutates the
    module-level COLORS dict so widgets that read it during rebuild pick up
    the new palette on next repaint. Returns the stylesheet applied."""
    global COLORS
    if mode == 'light':
        COLORS.update(LIGHT_COLORS)
        app.setStyleSheet(LIGHT_STYLESHEET)
        return LIGHT_STYLESHEET
    COLORS.update(DARK_COLORS)
    app.setStyleSheet(DARK_STYLESHEET)
    return DARK_STYLESHEET
