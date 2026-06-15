"""Custom left MINIMIZE rail — AmiBroker-style vertical tabs.

Replaces ADS auto-hide for the ─ minimize verb. A minimized tool / chart window / side panel is
HIDDEN and represented by a vertical-text button on this thin left strip; clicking the button
restores it full-size. ADS auto-hide proved unstable with several auto-hide containers on one
sidebar (it freed docks once the 4th was added, and its fixed-width slide-out flyout left empty
space on restore), so minimize is handled entirely here instead — no ADS auto-hide, no dock
deletion, no empty-space flyout, and the widget's state is preserved (it is only hidden).

The rail is a vertical QToolBar docked in the QMainWindow's LeftToolBarArea (left of the ADS
central widget); it hides itself when empty so it costs nothing until something is minimized.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme


class _VTabButton(QtWidgets.QToolButton):
    """One vertical tab: the tool icon on top, the label drawn rotated 90° below it."""

    def __init__(self, label: str, icon: QtGui.QIcon | None, parent=None):
        super().__init__(parent)
        self._label = label
        self._vicon = icon
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"Restore {label}")
        self.setAutoRaise(True)
        text_w = self.fontMetrics().horizontalAdvance(label)
        self._icon_h = 16 if (icon is not None and not icon.isNull()) else 0
        self.setFixedWidth(24)
        self.setFixedHeight(10 + self._icon_h + (8 if self._icon_h else 0) + text_w + 12)
        self.setStyleSheet(
            "QToolButton{border:none;background:transparent;border-radius:4px;}"
            f"QToolButton:hover{{background:{theme.HOVER};}}"
        )

    def paintEvent(self, _ev):  # noqa: N802 - Qt override
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        w = self.width()
        y = 8
        if self._icon_h:
            self._vicon.paint(p, (w - 16) // 2, y, 16, 16)
            y += self._icon_h + 8
        p.setPen(QtGui.QColor(theme.TEXT2))
        # rotate 90° clockwise: text reads top→bottom down the strip, centred on the width
        p.translate((w + p.fontMetrics().ascent()) / 2 - 1, y)
        p.rotate(90)
        p.drawText(0, 0, self._label)
        p.end()


class MinimizedRail(QtWidgets.QToolBar):
    """Thin vertical rail of restore tabs for minimized windows/panels. Hidden when empty."""

    def __init__(self, parent=None):
        super().__init__("Minimized", parent)
        self.setObjectName("minimizeRail")
        self.setMovable(False)
        self.setFloatable(False)
        self.setOrientation(QtCore.Qt.Vertical)
        self.setIconSize(QtCore.QSize(16, 16))
        self.setStyleSheet(
            f"QToolBar#minimizeRail{{background:{theme.PANEL};border:none;"
            f"border-right:1px solid {theme.BORDER};spacing:2px;padding:4px 1px;}}"
        )
        self._items: dict[str, tuple[QtGui.QAction, _VTabButton]] = {}
        self.hide()

    def add(self, key: str, label: str, icon, on_restore: Callable[[], None]) -> None:
        """Add (or refresh) a tab for ``key``. Clicking it runs ``on_restore`` then drops the tab."""
        if key in self._items:                       # already minimized — refresh the callback
            self.remove(key)
        btn = _VTabButton(label, icon, self)
        btn.clicked.connect(lambda: (self.remove(key), on_restore()))
        act = self.addWidget(btn)
        self._items[key] = (act, btn)
        self.show()

    def remove(self, key: str) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            act, btn = item
            self.removeAction(act)
            btn.deleteLater()
        if not self._items:
            self.hide()

    def has(self, key: str) -> bool:
        return key in self._items
