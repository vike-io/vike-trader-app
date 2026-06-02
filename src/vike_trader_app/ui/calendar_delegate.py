"""Custom painting helpers for the calendar tree: the 1–3 bar importance glyph and
beat/miss value coloring. Kept as free functions so they're unit-testable without a view.
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui

from . import theme

_BAR_COLORS = {0: theme.TEXT3, 1: theme.WARN, 2: theme.DOWN}


def importance_bar_pixmap(importance: int) -> QtGui.QPixmap:
    """Three ascending bars; `importance`+1 are lit in the level color, rest dim."""
    w, h = 18, 14
    pm = QtGui.QPixmap(w, h)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    lit = _BAR_COLORS.get(importance, theme.TEXT3)
    heights = [5, 9, 13]
    for i, bh in enumerate(heights):
        on = i <= importance
        p.fillRect(QtCore.QRect(1 + i * 6, h - bh, 4, bh),
                   QtGui.QColor(lit if on else theme.BORDER2))
    p.end()
    return pm


def value_color(actual: float | None, forecast: float | None) -> str:
    if actual is None or forecast is None:
        return theme.TEXT
    if actual > forecast:
        return theme.UP
    if actual < forecast:
        return theme.DOWN
    return theme.TEXT
