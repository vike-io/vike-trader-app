"""Options-chain space: CALLS | STRIKE | PUTS grid with controls.

Renders purely from an `OptionChain` pushed in via `set_chain()` — no network here
(the `OptionsService` owns fetching). Errors are shown on a status label, never a
modal (headless-CI hang lesson).
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..data.options.model import OptionChain, OptionQuote
from . import theme

# per-side columns (center is "Strike")
_SIDE_COLS = ["Bid", "Ask", "Mark", "IV", "Δ", "Γ", "Θ", "V", "OI", "Vol"]
# Calls and puts show the same columns; kept as separate lists so the two sides can
# diverge later without reshuffling indices. Don't mutate these in place.
CALL_COLS = list(_SIDE_COLS)
PUT_COLS = list(_SIDE_COLS)
COLS = CALL_COLS + ["Strike"] + PUT_COLS
_STRIKE_COL = len(CALL_COLS)
_DASH = "—"

# how each per-side column reads an OptionQuote (attr, kind) — kind drives formatting
_FIELD = {
    "Bid": ("bid", "px"), "Ask": ("ask", "px"), "Mark": ("mark", "px"),
    "IV": ("iv", "pct"), "Δ": ("delta", "g"), "Γ": ("gamma", "g"),
    "Θ": ("theta", "g"), "V": ("vega", "g"), "OI": ("open_interest", "int"),
    "Vol": ("volume", "int"),
}


def _fmt(value, kind: str) -> str:
    if value is None:
        return _DASH
    if kind == "pct":
        return f"{value * 100:.2f}%"
    if kind == "int":
        return f"{value:,.0f}"
    if kind == "g":
        return f"{value:.3f}"
    return f"{value:,.2f}"  # px


class OptionsTab(QtWidgets.QWidget):
    """The Options space (full-width central tab)."""

    underlyingChanged = QtCore.Signal(str)
    expiryChanged = QtCore.Signal(str)       # expiry ISO date
    refreshRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.underlying = QtWidgets.QComboBox()
        self.underlying.setEditable(True)
        self.underlying.addItems(["BTC", "ETH", "SOL", "^VIX", "SPY", "QQQ", "AAPL"])
        self.expiry = QtWidgets.QComboBox()
        self.strikes = QtWidgets.QComboBox()
        self.strikes.addItems(["±6", "±12", "All"])
        self.strikes.setCurrentText("±12")
        self.refresh_btn = QtWidgets.QToolButton()
        self.refresh_btn.setText("Refresh")
        self.status_label = QtWidgets.QLabel("—")
        self.status_label.setStyleSheet(f"color:{theme.TEXT3};border:none;")
        for w in (QtWidgets.QLabel("Symbol"), self.underlying, QtWidgets.QLabel("Expiry"),
                  self.expiry, QtWidgets.QLabel("Strikes"), self.strikes, self.refresh_btn):
            controls.addWidget(w)
        controls.addStretch(1)
        controls.addWidget(self.status_label)
        root.addLayout(controls)

        self.table = QtWidgets.QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        # 21 narrow columns: fit each to its content, pin the central strike column.
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(_STRIKE_COL, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(_STRIKE_COL, 76)
        root.addWidget(self.table, 1)

        # Picking a preset fires `activated`; typing a custom ticker + Enter fires
        # the line-edit's `returnPressed` (activated alone misses novel text).
        self.underlying.activated.connect(self._emit_underlying)
        self.underlying.lineEdit().returnPressed.connect(self._emit_underlying)
        self.expiry.activated.connect(self._emit_expiry)
        self.refresh_btn.clicked.connect(self.refreshRequested.emit)

    def _emit_underlying(self) -> None:
        self.underlyingChanged.emit(self.underlying.currentText())

    def _emit_expiry(self) -> None:
        iso = self.expiry.currentData()
        if iso:
            self.expiryChanged.emit(iso)

    def strikes_value(self) -> int | None:
        return {"±6": 6, "±12": 12, "All": None}[self.strikes.currentText()]

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_expiries(self, expiries) -> None:
        self.expiry.blockSignals(True)
        self.expiry.clear()
        for e in expiries:
            self.expiry.addItem(f"{e.label}  ·  {e.dte}DTE", e.date)
        self.expiry.blockSignals(False)

    def set_chain(self, chain: OptionChain) -> None:
        self.table.setRowCount(len(chain.rows))
        for r, row in enumerate(chain.rows):
            self._fill_side(r, row.call, CALL_COLS, 0, theme.UP)
            self._strike_cell(r, row.strike)
            self._fill_side(r, row.put, PUT_COLS, _STRIKE_COL + 1, theme.DOWN)
        px = "—" if chain.underlying_price is None else f"{chain.underlying_price:,.2f}"
        self.set_status(f"{chain.underlying} {px}  ·  {chain.source}  ·  {chain.expiry.label}")

    def _fill_side(self, r: int, q: OptionQuote | None, cols: list[str], base: int, color: str) -> None:
        for i, name in enumerate(cols):
            attr, kind = _FIELD[name]
            text = _DASH if q is None else _fmt(getattr(q, attr), kind)
            item = QtWidgets.QTableWidgetItem(text)
            item.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            item.setForeground(QtGui.QColor(color))
            if q is not None and q.in_the_money:
                item.setBackground(QtGui.QColor(theme.PANEL2))
            self.table.setItem(r, base + i, item)

    def _strike_cell(self, r: int, strike: float) -> None:
        item = QtWidgets.QTableWidgetItem(f"{strike:,.2f}")
        item.setTextAlignment(QtCore.Qt.AlignCenter)
        item.setForeground(QtGui.QColor(theme.TEXT))
        item.setBackground(QtGui.QColor(theme.PANEL))
        self.table.setItem(r, _STRIKE_COL, item)
