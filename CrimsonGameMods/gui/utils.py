from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, QRect, QSize
from PySide6.QtWidgets import (
    QLabel, QLayout, QPushButton, QSizePolicy, QStyle, QTableWidgetItem,
)
from gui.theme import COLORS


class FlowLayout(QLayout):
    """Wrap-capable horizontal layout — children flow to next row when the
    parent gets narrower. Based on the Qt FlowLayout documentation example.

    Use this instead of QHBoxLayout for toolbars with many buttons so they
    wrap instead of forcing a super-wide window.
    """

    def __init__(self, parent=None, margin: int = 0,
                 h_spacing: int = 4, v_spacing: int = 4):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: list = []

    def __del__(self):
        while self._items:
            self._items.pop()

    def addItem(self, item):
        self._items.append(item)

    def addWidget(self, widget, stretch: int = 0, alignment=None) -> None:
        # Drop-in replacement for QHBoxLayout.addWidget — accepts and ignores
        # the stretch/alignment args (Flow wraps naturally; stretch is moot).
        super().addWidget(widget)

    def addLayout(self, layout, stretch: int = 0) -> None:
        super().addItem(layout)

    def addStretch(self, stretch: int = 0) -> None:
        # Flow wraps naturally — addStretch is a no-op in a wrap layout.
        pass

    def addSpacing(self, _size: int) -> None:
        pass

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._doLayout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._doLayout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _doLayout(self, rect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            wid = item.widget()
            space_x = self._h_space
            space_y = self._v_space
            if wid is not None:
                style = wid.style()
                space_x = max(space_x, style.layoutSpacing(
                    QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Horizontal))
                space_y = max(space_y, style.layoutSpacing(
                    QSizePolicy.PushButton, QSizePolicy.PushButton, Qt.Vertical))
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + m.bottom()


def make_scope_label(scope: str) -> QLabel:
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
    item = QTableWidgetItem()
    item.setData(Qt.DisplayRole, value)
    return item


def make_help_btn(guide_key: str, show_guide_fn) -> QPushButton:
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
