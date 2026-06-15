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
    """One vertical TEXT tab (AmiBroker-style): just the rotated label, no icon."""

    def __init__(self, label: str, icon: QtGui.QIcon | None = None, parent=None):
        super().__init__(parent)
        self._label = label                       # icon arg accepted but unused — text-only rail
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip(f"Restore {label}")
        self.setAutoRaise(True)
        text_w = self.fontMetrics().horizontalAdvance(label)
        self.setFixedWidth(24)
        self.setFixedHeight(12 + text_w + 12)
        self.setStyleSheet(
            "QToolButton{border:none;background:transparent;border-radius:4px;}"
            f"QToolButton:hover{{background:{theme.HOVER};}}"
        )

    def paintEvent(self, _ev):  # noqa: N802 - Qt override
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setRenderHint(QtGui.QPainter.TextAntialiasing)
        w, h = self.width(), self.height()
        fm = p.fontMetrics()
        p.setPen(QtGui.QColor(theme.TEXT2))
        # Vertical label reads BOTTOM-TO-TOP (AmiBroker / VS-Code left-rail convention), anchored at
        # the bottom and running up; drawText's y-offset of capHeight/2 centres it across the strip.
        p.save()
        p.translate(w / 2.0, h - 10)
        p.rotate(-90)
        p.drawText(0, fm.capHeight() / 2.0, self._label)
        p.restore()
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
