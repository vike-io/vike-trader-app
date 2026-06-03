"""Shared dropdown/popup building blocks.

One embedded search field and one section-header style so every dropdown in the app looks
the same. See docs/research/2026-06-03-dropdown-unification-report.md for the full spec.
Popup surfaces themselves use the unified tokens directly: background ``theme.SURFACE``,
``1px solid theme.BORDER``, ``theme.RADIUS_POPUP`` radius, ``theme.DROPDOWN_ITEM_PAD`` item
rows, and ``theme.apply_popup_shadow()`` on frameless popups.
"""

from __future__ import annotations

from PySide6 import QtWidgets

from . import theme


def make_search(placeholder: str = "Search") -> QtWidgets.QLineEdit:
    """The single embedded search field for dropdowns: inset (BG) field, RADIUS_MD, accent focus."""
    e = QtWidgets.QLineEdit()
    e.setPlaceholderText(placeholder)
    e.setClearButtonEnabled(True)
    e.setStyleSheet(
        f"QLineEdit{{background:{theme.BG};color:{theme.TEXT};border:1px solid {theme.BORDER};"
        f"border-radius:{theme.RADIUS_MD}px;padding:7px 11px;font-size:13px;}}"
        f"QLineEdit:focus{{border-color:{theme.ACCENT};}}"
    )
    return e


def section_label(text: str) -> QtWidgets.QLabel:
    """The single section-header style for grouped dropdowns/lists: tiny uppercase TEXT3 caption."""
    lbl = QtWidgets.QLabel(text.upper())
    lbl.setStyleSheet(
        f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:0.5px;"
        f"background:transparent;border:none;padding:4px 6px 2px;"
    )
    return lbl
