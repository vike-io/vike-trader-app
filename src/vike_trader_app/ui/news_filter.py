"""TradingView-style multi-select filter dropdown for the News space.

A pill button ("Provider ▾" / "Provider (3) ▾") that opens a dark popover with a bold header,
an inner Search box, a scrollable list of checkbox rows, and a "Select all" footer — matching
TV's news-flow filter dropdowns (14px header/600, 14px rows, 32px row height). Empty selection
means "no constraint" (all). Emits ``selectionChanged`` when the set of checked options changes.
"""
from __future__ import annotations

import time

from PySide6 import QtCore, QtWidgets

from . import theme


class _Popover(QtWidgets.QFrame):
    """The dark dropdown panel (header + search + checkbox list + Select all)."""

    selectionChanged = QtCore.Signal()

    def __init__(self, title: str, options: list[str], parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        self._on_hide = None
        self.setObjectName("filterPop")
        self.setStyleSheet(
            f"#filterPop{{background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:10px;}}"
            f"#filterPop QLabel#hdr{{color:{theme.TEXT};font-size:14px;font-weight:600;"
            f"background:transparent;border:none;}}"
            f"#filterPop QLineEdit{{background:{theme.RAISE};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:7px;padding:6px 10px;font-size:14px;}}"
            f"#filterPop QCheckBox{{color:{theme.TEXT2};font-size:14px;spacing:10px;"
            f"padding:6px 6px;border-radius:6px;}}"
            f"#filterPop QCheckBox:hover{{color:{theme.TEXT};background:{theme.HOVER};}}"
            f"#filterPop QCheckBox::indicator{{width:16px;height:16px;border:1px solid {theme.TEXT3};"
            f"border-radius:4px;background:transparent;}}"
            f"#filterPop QCheckBox::indicator:checked{{background:{theme.ACCENT};"
            f"border-color:{theme.ACCENT};}}"
            f"#filterPop QScrollArea{{border:none;background:transparent;}}"
            f"#filterPop #divider{{background:{theme.BORDER};}}")

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(8)

        hdr = QtWidgets.QLabel(title)
        hdr.setObjectName("hdr")
        v.addWidget(hdr)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search")
        self._search.textChanged.connect(self._apply_filter)
        v.addWidget(self._search)

        area = QtWidgets.QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(1)
        self._boxes: dict[str, QtWidgets.QCheckBox] = {}
        for opt in options:
            cb = QtWidgets.QCheckBox(opt)
            cb.setCursor(QtCore.Qt.PointingHandCursor)
            cb.toggled.connect(self._on_toggled)
            bl.addWidget(cb)
            self._boxes[opt] = cb
        bl.addStretch(1)
        area.setWidget(body)
        v.addWidget(area, 1)

        divider = QtWidgets.QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        v.addWidget(divider)

        self._all = QtWidgets.QCheckBox("Select all")
        self._all.setCursor(QtCore.Qt.PointingHandCursor)
        self._all.clicked.connect(self._on_select_all_clicked)
        v.addWidget(self._all)

        self.setFixedWidth(224)
        self.setMaximumHeight(min(560, 132 + 30 * len(options)))
        self._sync_select_all()

    # ---- internal ----
    def _apply_filter(self, text: str) -> None:
        t = text.strip().lower()
        for opt, cb in self._boxes.items():
            cb.setVisible(not t or t in opt.lower())

    def _on_toggled(self, _checked: bool) -> None:
        self._sync_select_all()
        self.selectionChanged.emit()

    def _on_select_all_clicked(self) -> None:
        target = not all(cb.isChecked() for cb in self._boxes.values())
        for cb in self._boxes.values():
            cb.blockSignals(True)
            cb.setChecked(target)
            cb.blockSignals(False)
        self._sync_select_all()
        self.selectionChanged.emit()

    def _sync_select_all(self) -> None:
        checked = sum(cb.isChecked() for cb in self._boxes.values())
        self._all.blockSignals(True)
        if checked == 0:
            self._all.setCheckState(QtCore.Qt.Unchecked)
        elif checked == len(self._boxes):
            self._all.setCheckState(QtCore.Qt.Checked)
        else:
            self._all.setCheckState(QtCore.Qt.PartiallyChecked)
        self._all.blockSignals(False)

    def hideEvent(self, e):  # noqa: N802 - Qt override; notify the owner button it just closed
        super().hideEvent(e)
        if self._on_hide:
            self._on_hide()

    # ---- API ----
    def selected(self) -> set[str]:
        return {opt for opt, cb in self._boxes.items() if cb.isChecked()}

    def set_selected(self, values: set[str]) -> None:
        for opt, cb in self._boxes.items():
            cb.blockSignals(True)
            cb.setChecked(opt in values)
            cb.blockSignals(False)
        self._sync_select_all()


class MultiSelectFilter(QtWidgets.QToolButton):
    """A TV-style pill that opens a :class:`_Popover`. Empty selection == no constraint (all)."""

    selectionChanged = QtCore.Signal()

    def __init__(self, label: str, options: list[str], parent=None):
        super().__init__(parent)
        self._label = label
        self._options = list(options)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.setStyleSheet(
            f"QToolButton{{background:{theme.RAISE};color:{theme.TEXT2};"
            f"border:1px solid {theme.BORDER};border-radius:8px;padding:7px 14px;font-size:13px;}}"
            f"QToolButton:hover{{color:{theme.TEXT};border-color:{theme.TEXT3};}}"
            "QToolButton::menu-indicator{width:0px;}")
        self._pop = _Popover(label, options, self)
        self._pop.selectionChanged.connect(self._on_changed)
        self._pop._on_hide = self._note_closed
        self._closed_at = 0.0
        self.clicked.connect(self._toggle_pop)
        self._refresh_text()

    def _note_closed(self) -> None:
        self._closed_at = time.monotonic()

    def _toggle_pop(self) -> None:
        if self._pop.isVisible():
            self._pop.hide()
            return
        # A Qt.Popup grabs the mouse: clicking the open pill first auto-hides the popover, then
        # this click arrives — without this guard it would immediately re-open. Ignore a click
        # that lands right after the popover closed itself.
        if time.monotonic() - self._closed_at < 0.20:
            return
        below = self.mapToGlobal(QtCore.QPoint(0, self.height() + 4))
        self._pop.move(below)
        self._pop.show()

    def _on_changed(self) -> None:
        self._refresh_text()
        self.selectionChanged.emit()

    def _refresh_text(self) -> None:
        n = len(self._pop.selected())
        self.setText(f"{self._label} ({n})  ▾" if n else f"{self._label}  ▾")

    def selected(self) -> set[str]:
        return self._pop.selected()

    def set_selected(self, values: set[str]) -> None:
        self._pop.set_selected(set(values))
        self._refresh_text()
        self.selectionChanged.emit()   # programmatic change notifies (like QComboBox.setCurrentText)
