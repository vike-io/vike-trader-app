"""TradingView-style 'Select countries' modal for the Economic calendar.

Search box, selected-chips + Clear, quick-picks (Entire world / Top 20 economies), region
groups with flag-circle items, Apply/Cancel. Returns a set of currency codes (None == all).
"""
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..data.calendar.taxonomy import (
    ALL_CURRENCIES, COUNTRY_REGIONS, TOP20_ECONOMIES, currency_country)
from . import theme
from .economic_calendar import country_chip_pixmap   # reuse the flag-circle pixmap


class SelectCountriesDialog(QtWidgets.QDialog):
    """Modal country picker. `selected_countries()` -> set[str] | None (None == 'all')."""

    def __init__(self, selected: set[str] | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select countries")
        self.setModal(True)
        self.resize(440, 580)
        self._selected: set[str] = set(selected) if selected else set()

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        title = QtWidgets.QLabel("Select countries")
        title.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;border:none;")
        root.addWidget(title)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search")
        self._search.textChanged.connect(self._apply_filter)
        root.addWidget(self._search)

        # selected chips + Clear
        chips_row = QtWidgets.QHBoxLayout()
        chips_row.setSpacing(8)
        self._chips_box = QtWidgets.QWidget()
        self._chips_lay = QtWidgets.QHBoxLayout(self._chips_box)
        self._chips_lay.setContentsMargins(0, 0, 0, 0)
        self._chips_lay.setSpacing(6)
        clear = QtWidgets.QPushButton("Clear")
        clear.clicked.connect(lambda: self._set_all(set()))
        chips_row.addWidget(self._chips_box, 1)
        chips_row.addWidget(clear)
        root.addLayout(chips_row)

        # quick-picks
        qp = QtWidgets.QHBoxLayout()
        qp.setSpacing(8)
        b_world = QtWidgets.QPushButton("🌐  Entire world")
        b_world.clicked.connect(lambda: self._set_all(set(ALL_CURRENCIES)))
        b_top = QtWidgets.QPushButton("G20  Top 20 economies")
        b_top.clicked.connect(lambda: self._set_all(set(TOP20_ECONOMIES)))
        qp.addWidget(b_world)
        qp.addWidget(b_top)
        qp.addStretch(1)
        root.addLayout(qp)

        # region groups -> checkable, flag-iconed list
        self._list = QtWidgets.QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:{theme.PANEL};border:1px solid {theme.BORDER};"
            f"border-radius:8px;outline:none;}} QListWidget::item{{padding:6px 8px;}}")
        self._items: dict[str, QtWidgets.QListWidgetItem] = {}
        for region, currencies in COUNTRY_REGIONS.items():
            hdr = QtWidgets.QListWidgetItem(region.upper())
            hdr.setFlags(QtCore.Qt.NoItemFlags)
            f = hdr.font()
            f.setBold(True)
            f.setPointSize(max(f.pointSize() - 1, 7))
            hdr.setFont(f)
            hdr.setForeground(QtGui.QColor(theme.TEXT3))
            self._list.addItem(hdr)
            for cur in currencies:
                name, iso = currency_country(cur)
                it = QtWidgets.QListWidgetItem(QtGui.QIcon(country_chip_pixmap(iso)), f"{name}")
                it.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                it.setCheckState(QtCore.Qt.Checked if cur in self._selected else QtCore.Qt.Unchecked)
                it.setData(QtCore.Qt.UserRole, cur)
                self._list.addItem(it)
                self._items[cur] = it
        self._list.itemChanged.connect(self._on_item_changed)
        root.addWidget(self._list, 1)

        # Apply / Cancel
        btns = QtWidgets.QHBoxLayout()
        btns.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        apply_btn = QtWidgets.QPushButton("Apply")
        apply_btn.setDefault(True)
        apply_btn.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(apply_btn)
        root.addLayout(btns)

        self._refresh_chips()

    def _on_item_changed(self, it: QtWidgets.QListWidgetItem) -> None:
        cur = it.data(QtCore.Qt.UserRole)
        if cur is None:
            return
        if it.checkState() == QtCore.Qt.Checked:
            self._selected.add(cur)
        else:
            self._selected.discard(cur)
        self._refresh_chips()

    def _set_all(self, currencies: set[str]) -> None:
        self._list.blockSignals(True)
        for cur, it in self._items.items():
            it.setCheckState(QtCore.Qt.Checked if cur in currencies else QtCore.Qt.Unchecked)
        self._list.blockSignals(False)
        self._selected = set(currencies)
        self._refresh_chips()

    def _apply_filter(self, text: str) -> None:
        t = text.strip().lower()
        for cur, it in self._items.items():
            it.setHidden(bool(t) and t not in it.text().lower() and t not in cur.lower())

    def _refresh_chips(self) -> None:
        # clear existing chip widgets
        while self._chips_lay.count():
            item = self._chips_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._selected:
            lbl = QtWidgets.QLabel("All countries")
            lbl.setStyleSheet(f"color:{theme.TEXT3};font-size:13px;border:none;")
            self._chips_lay.addWidget(lbl)
            self._chips_lay.addStretch(1)
            return
        codes = sorted(self._selected)
        shown, overflow = codes[:7], len(codes) - 7
        for cur in shown:
            chip = QtWidgets.QLabel(cur)
            chip.setStyleSheet(
                f"background:{theme.RAISE};color:{theme.TEXT};font-size:12px;"
                f"padding:3px 9px;border-radius:9px;border:none;")
            self._chips_lay.addWidget(chip)
        if overflow > 0:
            more = QtWidgets.QLabel(f"+{overflow}")
            more.setStyleSheet(f"color:{theme.TEXT3};font-size:12px;border:none;")
            self._chips_lay.addWidget(more)
        self._chips_lay.addStretch(1)

    def selected_countries(self) -> set[str] | None:
        return self._selected or None
