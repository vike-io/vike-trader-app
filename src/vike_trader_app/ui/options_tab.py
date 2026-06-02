"""Options-chain space: TradingView/TradeStation-style CALLS | STRIKE·IV | PUTS grid.

Renders purely from an `OptionChain` pushed in via `set_chain()` — no network here (the
`OptionsService` owns fetching). Two views via a toggle: "Chain" (LTP/Theor/Spread/Bid%/Ask%/
Distance/Rel dist/Ann%/Volume, like the screenshot) and "Greeks" (Δ/Γ/Θ/V). Volume cells get a
blue (calls) / red (puts) magnitude bar; ITM cells are hatch-shaded; an ATM marker row shows
the underlying price mid-table. Errors go to a status label, never a modal.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..data.options import columns as C
from ..data.options.model import OptionChain, StrikeRow
from . import theme

_CALL_BAR = "#4c9ffe"   # TradingView-ish blue for call volume bars
_PUT_BAR = theme.DOWN   # red for put volume bars


class _VolumeBarDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a thin magnitude bar under the two Volume columns (blue calls / red puts)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.call_col = -1
        self.put_col = -1

    def paint(self, painter, option, index) -> None:
        col = index.column()
        if col in (self.call_col, self.put_col):
            frac = index.data(QtCore.Qt.UserRole) or 0.0
            if frac > 0:
                painter.save()
                r = option.rect
                color = QtGui.QColor(_CALL_BAR if col == self.call_col else _PUT_BAR)
                color.setAlpha(70)
                width = int((r.width() - 8) * min(frac, 1.0))
                painter.fillRect(r.x() + 4, r.bottom() - 5, width, 2, color)
                painter.restore()
        super().paint(painter, option, index)


def _columns(view: str) -> tuple[list[str], list[str], list[str]]:
    """(call_fields outer->centre, put_fields centre->outer, header labels) for a view."""
    side = C.CHAIN_FIELDS if view == "chain" else C.GREEKS_FIELDS
    call_fields = list(reversed(side))
    put_fields = list(side)
    headers = ([C.HEADERS[f] for f in call_fields] + ["Strike", "IV"]
               + [C.HEADERS[f] for f in put_fields])
    return call_fields, put_fields, headers


class OptionsTab(QtWidgets.QWidget):
    """The Options space (full-width central tab)."""

    underlyingChanged = QtCore.Signal(str)
    expiryChanged = QtCore.Signal(str)       # expiry ISO date
    refreshRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._chain: OptionChain | None = None
        self._view = "chain"
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.underlying = QtWidgets.QComboBox()
        self.underlying.setEditable(True)
        self.underlying.addItems(["BTC", "ETH", "SOL", "^VIX", "SPY", "QQQ", "AAPL", "MSFT"])
        self.expiry = QtWidgets.QComboBox()
        self.strikes = QtWidgets.QComboBox()
        self.strikes.addItems(["±6", "±12", "All"])
        self.strikes.setCurrentText("±12")
        self.view_toggle = QtWidgets.QComboBox()
        self.view_toggle.addItems(["Chain", "Greeks"])
        self.refresh_btn = QtWidgets.QToolButton()
        self.refresh_btn.setText("Refresh")
        self.status_label = QtWidgets.QLabel("—")
        self.status_label.setStyleSheet(f"color:{theme.TEXT3};border:none;")
        for w in (QtWidgets.QLabel("Symbol"), self.underlying, QtWidgets.QLabel("Expiry"),
                  self.expiry, QtWidgets.QLabel("Strikes"), self.strikes,
                  QtWidgets.QLabel("View"), self.view_toggle, self.refresh_btn):
            controls.addWidget(w)
        controls.addStretch(1)
        controls.addWidget(self.status_label)
        root.addLayout(controls)

        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self._bar = _VolumeBarDelegate(self.table)
        self.table.setItemDelegate(self._bar)
        root.addWidget(self.table, 1)

        # Picking a preset fires `activated`; typing a custom ticker + Enter fires the
        # line-edit's `returnPressed` (activated alone misses novel text).
        self.underlying.activated.connect(self._emit_underlying)
        self.underlying.lineEdit().returnPressed.connect(self._emit_underlying)
        self.expiry.activated.connect(self._emit_expiry)
        self.strikes.activated.connect(self.refreshRequested.emit)
        self.view_toggle.activated.connect(self._on_view_changed)
        self.refresh_btn.clicked.connect(self.refreshRequested.emit)

    # --- signals out ---------------------------------------------------------
    def _emit_underlying(self) -> None:
        self.underlyingChanged.emit(self.underlying.currentText())

    def _emit_expiry(self) -> None:
        iso = self.expiry.currentData()
        if iso:
            self.expiryChanged.emit(iso)

    def _on_view_changed(self) -> None:
        self._view = "greeks" if self.view_toggle.currentText() == "Greeks" else "chain"
        self._render()  # re-render the last chain with the new column set

    # --- inputs --------------------------------------------------------------
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
        self._chain = chain
        self._render()
        px = "—" if chain.underlying_price is None else f"{chain.underlying_price:,.2f}"
        self.set_status(f"{chain.underlying} {px}  ·  {chain.source}  ·  {chain.expiry.label}")

    # --- rendering -----------------------------------------------------------
    def _render(self) -> None:
        chain = self._chain
        call_fields, put_fields, headers = _columns(self._view)
        self.table.clearSpans()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        strike_col = len(call_fields)
        ncols = len(headers)
        self._bar.call_col = call_fields.index("volume")
        self._bar.put_col = strike_col + 2 + put_fields.index("volume")
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(strike_col, QtWidgets.QHeaderView.Fixed)
        self.table.setColumnWidth(strike_col, 76)
        if chain is None:
            return

        spot, dte = chain.underlying_price, chain.expiry.dte
        vols = [q.volume for r in chain.rows for q in (r.call, r.put) if q and q.volume]
        maxvol = max(vols) if vols else 0.0

        self.table.setRowCount(len(chain.rows))
        for ri, row in enumerate(chain.rows):
            self._fill_side(ri, row, call_fields, 0, "C", spot, dte, maxvol)
            self._strike_iv(ri, row, strike_col)
            self._fill_side(ri, row, put_fields, strike_col + 2, "P", spot, dte, maxvol)
        self._insert_atm_row(chain, ncols)

    def _fill_side(self, ri: int, row: StrikeRow, fields: list[str], base: int, side: str,
                   spot: float | None, dte: int, maxvol: float) -> None:
        q = row.call if side == "C" else row.put
        itm = q is not None and spot is not None and (
            row.strike < spot if side == "C" else row.strike > spot)
        for i, field in enumerate(fields):
            raw = C.cell_value(field, q, spot, dte)
            item = QtWidgets.QTableWidgetItem(C.fmt(raw, field))
            item.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            if field == "volume":
                item.setData(QtCore.Qt.UserRole, (raw / maxvol) if (raw and maxvol) else 0.0)
                item.setForeground(QtGui.QColor(_CALL_BAR if side == "C" else _PUT_BAR))
            else:
                item.setForeground(QtGui.QColor(theme.TEXT2))
            if itm:
                item.setBackground(QtGui.QBrush(QtGui.QColor(theme.PANEL2), QtCore.Qt.BDiagPattern))
            self.table.setItem(ri, base + i, item)

    def _strike_iv(self, ri: int, row: StrikeRow, strike_col: int) -> None:
        strike = QtWidgets.QTableWidgetItem(f"{row.strike:,.2f}")
        strike.setTextAlignment(QtCore.Qt.AlignCenter)
        strike.setForeground(QtGui.QColor(theme.TEXT))
        strike.setBackground(QtGui.QColor(theme.PANEL))
        self.table.setItem(ri, strike_col, strike)
        iv = (row.call.iv if row.call else None)
        if iv is None and row.put:
            iv = row.put.iv
        iv_item = QtWidgets.QTableWidgetItem(C.fmt(iv, "iv"))
        iv_item.setTextAlignment(QtCore.Qt.AlignCenter)
        iv_item.setForeground(QtGui.QColor(theme.TEXT2))
        self.table.setItem(ri, strike_col + 1, iv_item)

    def _insert_atm_row(self, chain: OptionChain, ncols: int) -> None:
        spot = chain.underlying_price
        if spot is None:
            return
        pos = next((i for i, r in enumerate(chain.rows) if r.strike >= spot), len(chain.rows))
        self.table.insertRow(pos)
        self.table.setSpan(pos, 0, 1, ncols)
        marker = QtWidgets.QTableWidgetItem(f"{chain.underlying}   {spot:,.2f}")
        marker.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignCenter)
        marker.setForeground(QtGui.QColor(theme.ACCENT))   # accent text on a dark strip (TV-style)
        marker.setBackground(QtGui.QColor(theme.PANEL2))
        font = marker.font()
        font.setBold(True)
        marker.setFont(font)
        self.table.setItem(pos, 0, marker)
