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

_CALL_BAR = theme.BLUE   # blue magnitude bar for call volume
_PUT_BAR = theme.DOWN    # red magnitude bar for put volume
# TV/Deribit render chain values bright; theme.TEXT2 reads too dim next to the bright strike/IV.
# The color unification collapsed PANEL2/PANEL/RAISE onto SURFACE, which erased the grid's
# layering cues (ITM shading, ATM band, strike spine, section header all went invisible). These
# reintroduce them from the live four-tone surface scale (BG < SURFACE < HOVER < BORDER):
_CELL = theme.TEXT       # bright cell value, like TradingView
_ITM = theme.BORDER      # diagonal-hatch tone for in-the-money cells (visible over SURFACE)
_SPINE = theme.HOVER     # subtly raised centre Strike column (the spine)
_BAND = theme.HOVER      # ATM spot-price marker band
_SECTION = theme.BG      # recessed grouped-expiry section header


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
                color.setAlpha(120)
                width = int((r.width() - 8) * min(frac, 1.0))
                painter.fillRect(r.x() + 4, r.bottom() - 6, width, 3, color)
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
    rangeChanged = QtCore.Signal()           # expiration-range filter changed
    refreshRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._chain: OptionChain | None = None
        self._chains: list[OptionChain] | None = None   # grouped (multi-expiry) view
        self._group_rows: dict[int, list[int]] = {}      # header row -> its data/marker rows
        self._marker_rows: set[int] = set()              # spanned ATM-marker rows (span dropped while hidden)
        self._ncols = 0                                  # current column count (to restore marker spans)
        self._col_field: dict[int, tuple[str, str | None]] = {}  # column -> (field, side)
        self._sort: tuple | None = None                  # None=strike order; else (field, side, desc)
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
        self.exp_range = QtWidgets.QComboBox()
        self.exp_range.addItems(["Next 30d", "Next 60d", "Next 90d", "All"])
        self.strikes = QtWidgets.QComboBox()
        self.strikes.addItem("±6 strikes", 6)        # self-labeling like TV's "±6 strikes ▾"
        self.strikes.addItem("±12 strikes", 12)
        self.strikes.addItem("All strikes", None)
        self.strikes.setCurrentText("±12 strikes")
        self.view_toggle = QtWidgets.QComboBox()
        self.view_toggle.addItems(["Chain", "Greeks"])
        self.refresh_btn = QtWidgets.QToolButton()
        self.refresh_btn.setText("Refresh")
        self.status_label = QtWidgets.QLabel("—")
        self.status_label.setStyleSheet(f"color:{theme.TEXT3};border:none;background:transparent;")
        # TV-style: self-labeling dropdowns, left-aligned, no separate text labels.
        for w in (self.underlying, self.expiry, self.exp_range, self.strikes,
                  self.view_toggle, self.refresh_btn):
            controls.addWidget(w)
        controls.addStretch(1)
        controls.addWidget(self.status_label)
        barw = QtWidgets.QWidget()
        barw.setObjectName("optbar")
        barw.setStyleSheet(
            "#optbar QComboBox, #optbar QToolButton {"
            f" background:{theme.SURFACE}; color:{theme.TEXT2}; border:1px solid {theme.BORDER};"
            f" border-radius:{theme.RADIUS_MD}px; padding:6px 12px; font-size:13px; }}"
            "#optbar QComboBox:hover, #optbar QToolButton:hover {"
            f" color:{theme.TEXT}; border-color:{theme.TEXT3}; }}"
            "#optbar QComboBox::drop-down { border:none; width:18px; }")
        barw.setLayout(controls)
        root.addWidget(barw)

        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        # Match TV/Deribit's options-chain type scale: 13px cells (vs the global 12px), ~36px airy
        # rows (vs ~24px), and a normal-case 12px/600 header (vs the global tiny 10px uppercase).
        self.table.setStyleSheet(
            "QTableView{font-size:13px;}"
            f"QHeaderView::section{{background:{theme.CHART_BG};color:{theme.TEXT2};"
            "font-size:12px;font-weight:600;text-transform:none;letter-spacing:0;"
            f"padding:7px 10px;border:none;border-bottom:1px solid {theme.BORDER};}}")
        self.table.verticalHeader().setDefaultSectionSize(36)
        self._bar = _VolumeBarDelegate(self.table)
        self.table.setItemDelegate(self._bar)
        root.addWidget(self.table, 1)

        # Picking a preset fires `activated`; typing a custom ticker + Enter fires the
        # line-edit's `returnPressed` (activated alone misses novel text).
        self.underlying.activated.connect(self._emit_underlying)
        self.underlying.lineEdit().returnPressed.connect(self._emit_underlying)
        self.expiry.activated.connect(self._emit_expiry)
        self.strikes.activated.connect(self.refreshRequested.emit)
        self.exp_range.activated.connect(self.rangeChanged.emit)
        self.view_toggle.activated.connect(self._on_view_changed)
        self.refresh_btn.clicked.connect(self.refreshRequested.emit)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)

    # --- signals out ---------------------------------------------------------
    def _emit_underlying(self) -> None:
        self.underlyingChanged.emit(self.underlying.currentText())

    def _emit_expiry(self) -> None:
        iso = self.expiry.currentData()
        if iso:
            self.expiryChanged.emit(iso)

    def _on_view_changed(self) -> None:
        self._view = "greeks" if self.view_toggle.currentText() == "Greeks" else "chain"
        self._rerender()  # redraw with the new column set, in whichever mode is active

    def _rerender(self) -> None:
        self._render_groups() if self._chains is not None else self._render()

    # --- inputs --------------------------------------------------------------
    def strikes_value(self) -> int | None:
        return self.strikes.currentData()       # data carries the int window (6/12) or None=All

    def exp_range_days(self) -> int | None:
        """Max DTE to show as groups, or None for all expiries."""
        return {"Next 30d": 30, "Next 60d": 60, "Next 90d": 90, "All": None}[
            self.exp_range.currentText()]

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_expiries(self, expiries) -> None:
        self.expiry.blockSignals(True)
        self.expiry.clear()
        for e in expiries:
            self.expiry.addItem(f"{e.label}  ·  {e.dte}DTE", e.date)
        self.expiry.blockSignals(False)

    @staticmethod
    def _source_note(source: str) -> str:
        """Flag the free fallback so the data source (and how to upgrade) is always clear."""
        if source == "yfinance":
            return "  ·  free feed — greeks inferred (set options_stock_provider for exchange-grade)"
        return ""

    def set_chain(self, chain: OptionChain) -> None:
        """Single-expiry view (flat grid)."""
        self._chain, self._chains = chain, None
        self._render()
        px = "—" if chain.underlying_price is None else f"{chain.underlying_price:,.2f}"
        self.set_status(
            f"{chain.underlying} {px}  ·  {chain.source}  ·  {chain.expiry.label}"
            + self._source_note(chain.source))

    def set_chains(self, chains: list[OptionChain]) -> None:
        """Grouped view: each expiry as a collapsible section (first expanded)."""
        self._chains = chains
        self._render_groups()
        if chains:
            c = chains[0]
            px = "—" if c.underlying_price is None else f"{c.underlying_price:,.2f}"
            self.set_status(
                f"{c.underlying} {px}  ·  {c.source}  ·  {len(chains)} expiries"
                + self._source_note(c.source))

    # --- rendering -----------------------------------------------------------
    def _setup_columns(self) -> tuple[list[str], list[str], int, int]:
        """Reset the table to the active view's columns -> (call_fields, put_fields, ncols, strike_col)."""
        call_fields, put_fields, headers = _columns(self._view)
        self.table.clearSpans()
        self.table.setRowCount(0)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        strike_col = len(call_fields)
        self._bar.call_col = call_fields.index("volume")
        self._bar.put_col = strike_col + 2 + put_fields.index("volume")
        # map each column to (field, side) so header clicks know what to sort on
        self._col_field = {i: (f, "C") for i, f in enumerate(call_fields)}
        self._col_field[strike_col] = ("strike", None)
        self._col_field[strike_col + 1] = ("iv", None)
        self._col_field.update({strike_col + 2 + i: (f, "P") for i, f in enumerate(put_fields)})
        hh = self.table.horizontalHeader()
        # Content-size every column. Strike was Fixed at 76px — fine for VIX's 2-digit strikes, but
        # it truncated Deribit BTC's 5-digit strikes ("64,000…"); content-sizing fits both.
        hh.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        return call_fields, put_fields, len(headers), strike_col

    @staticmethod
    def _maxvol(chain: OptionChain) -> float:
        vols = [q.volume for r in chain.rows for q in (r.call, r.put) if q and q.volume]
        return max(vols) if vols else 0.0

    def _sorted_rows(self, chain: OptionChain) -> tuple[list[StrikeRow], bool]:
        """Rows in the active sort order. Returns (rows, show_atm) — the ATM marker only makes
        sense in strike order, so it's suppressed when sorting by another column."""
        if self._sort is None:
            return list(chain.rows), True
        field, side, desc = self._sort
        spot, dte = chain.underlying_price, chain.expiry.dte

        def val(r: StrikeRow):
            if field == "iv":
                return (r.call.iv if r.call else None) or (r.put.iv if r.put else None)
            return C.cell_value(field, r.call if side == "C" else r.put, spot, dte)

        have = [r for r in chain.rows if val(r) is not None]
        none = [r for r in chain.rows if val(r) is None]
        have.sort(key=val, reverse=desc)
        return have + none, False   # None-valued strikes always trail

    def _on_header_clicked(self, col: int) -> None:
        field, side = self._col_field.get(col, (None, None))
        if field is None:
            return
        if field == "strike":
            self._sort = None                       # back to the natural strike ladder
        elif self._sort and self._sort[0] == field and self._sort[1] == side:
            self._sort = (field, side, not self._sort[2])   # same column -> flip direction
        else:
            self._sort = (field, side, field in ("volume", "oi"))  # volume/OI default to desc
        self._rerender()

    def _render(self) -> None:
        self._group_rows = {}
        self._marker_rows = set()
        call_fields, put_fields, ncols, strike_col = self._setup_columns()
        self._ncols = ncols
        chain = self._chain
        if chain is None:
            return
        spot, dte, maxvol = chain.underlying_price, chain.expiry.dte, self._maxvol(chain)
        rows, show_atm = self._sorted_rows(chain)
        self.table.setRowCount(len(rows))
        for ri, row in enumerate(rows):
            self._fill_strike_row(ri, row, call_fields, put_fields, strike_col, spot, dte, maxvol)
        if show_atm and spot is not None:
            pos = next((i for i, r in enumerate(rows) if r.strike >= spot), len(rows))
            self._marker_row(chain, ncols, pos)

    def _render_groups(self) -> None:
        self._group_rows = {}
        self._marker_rows = set()
        call_fields, put_fields, ncols, strike_col = self._setup_columns()
        self._ncols = ncols
        for gi, chain in enumerate(self._chains or []):
            hdr = self.table.rowCount()
            self.table.insertRow(hdr)
            self.table.setSpan(hdr, 0, 1, ncols)
            self._set_group_header(hdr, chain, expanded=(gi == 0))
            rows = self._append_chain_rows(chain, call_fields, put_fields, ncols, strike_col)
            self._group_rows[hdr] = rows
            if gi != 0:  # collapse all but the nearest expiry by default
                self._set_rows_hidden(rows, True)

    def _append_chain_rows(self, chain: OptionChain, call_fields: list[str],
                           put_fields: list[str], ncols: int, strike_col: int) -> list[int]:
        """Append one chain's strike rows (+ its ATM marker) to the table; return their indices."""
        spot, dte, maxvol = chain.underlying_price, chain.expiry.dte, self._maxvol(chain)
        srows, show_atm = self._sorted_rows(chain)
        atm = (next((i for i, r in enumerate(srows) if r.strike >= spot), len(srows))
               if (show_atm and spot is not None) else -1)
        rows: list[int] = []
        for i, srow in enumerate(srows):
            if i == atm:
                rows.append(self._marker_row(chain, ncols, self.table.rowCount()))
            r = self.table.rowCount()
            self.table.insertRow(r)
            self._fill_strike_row(r, srow, call_fields, put_fields, strike_col, spot, dte, maxvol)
            rows.append(r)
        if show_atm and atm >= len(srows) and spot is not None:  # spot above every strike
            rows.append(self._marker_row(chain, ncols, self.table.rowCount()))
        return rows

    def _fill_strike_row(self, ri: int, row: StrikeRow, call_fields: list[str],
                         put_fields: list[str], strike_col: int, spot: float | None, dte: int,
                         maxvol: float) -> None:
        self._fill_side(ri, row, call_fields, 0, "C", spot, dte, maxvol)
        self._strike_iv(ri, row, strike_col)
        self._fill_side(ri, row, put_fields, strike_col + 2, "P", spot, dte, maxvol)

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
                item.setForeground(QtGui.QColor(_CELL))   # bright like TV, not dim TEXT2
            if itm:
                item.setBackground(QtGui.QBrush(QtGui.QColor(_ITM), QtCore.Qt.BDiagPattern))
            self.table.setItem(ri, base + i, item)

    def _strike_iv(self, ri: int, row: StrikeRow, strike_col: int) -> None:
        strike = QtWidgets.QTableWidgetItem(f"{row.strike:,.2f}")
        strike.setTextAlignment(QtCore.Qt.AlignCenter)
        strike.setForeground(QtGui.QColor(theme.TEXT))
        strike.setBackground(QtGui.QColor(_SPINE))
        self.table.setItem(ri, strike_col, strike)
        iv = (row.call.iv if row.call else None)
        if iv is None and row.put:
            iv = row.put.iv
        iv_item = QtWidgets.QTableWidgetItem(C.fmt(iv, "iv"))
        iv_item.setTextAlignment(QtCore.Qt.AlignCenter)
        iv_item.setForeground(QtGui.QColor(_CELL))
        self.table.setItem(ri, strike_col + 1, iv_item)

    def _marker_row(self, chain: OptionChain, ncols: int, pos: int) -> int:
        """Insert the full-width ATM underlying-price marker row at `pos`; return its index."""
        self.table.insertRow(pos)
        self.table.setSpan(pos, 0, 1, ncols)
        marker = QtWidgets.QTableWidgetItem(f"{chain.underlying}   {chain.underlying_price:,.2f}")
        marker.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignCenter)
        marker.setForeground(QtGui.QColor(theme.ACCENT))   # accent text on a raised band (TV-style)
        marker.setBackground(QtGui.QColor(_BAND))
        font = marker.font()
        font.setBold(True)
        marker.setFont(font)
        self.table.setItem(pos, 0, marker)
        self._marker_rows.add(pos)
        return pos

    def _set_group_header(self, hdr: int, chain: OptionChain, expanded: bool) -> None:
        glyph = "▾" if expanded else "▸"
        item = QtWidgets.QTableWidgetItem(
            f"   {glyph}   {chain.expiry.label}   ·   {chain.expiry.dte}DTE   ·   {chain.source}")
        item.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)
        item.setForeground(QtGui.QColor(theme.TEXT))
        item.setBackground(QtGui.QColor(_SECTION))
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.table.setItem(hdr, 0, item)

    def _on_cell_clicked(self, row: int, _col: int) -> None:
        if row in self._group_rows:   # clicking an expiry header toggles its section
            self._toggle_group(row)

    def _set_rows_hidden(self, rows: list[int], hide: bool) -> None:
        """Hide/show a group's rows. Qt still PAINTS hidden rows that carry a column span (the
        full-width ATM marker), so drop the span while hidden and restore it when shown."""
        for r in rows:
            if r in self._marker_rows:
                self.table.setSpan(r, 0, 1, 1 if hide else self._ncols)
            self.table.setRowHidden(r, hide)

    def _toggle_group(self, hdr: int) -> None:
        rows = self._group_rows.get(hdr) or []
        if not rows:
            return
        hide = not self.table.isRowHidden(rows[0])
        self._set_rows_hidden(rows, hide)
        item = self.table.item(hdr, 0)
        if item:
            item.setText(item.text().replace("▾", "▸") if hide else item.text().replace("▸", "▾"))
