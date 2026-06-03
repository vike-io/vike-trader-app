"""Options-chain space: Deribit/TradingView-style CALLS | STRIKE·IV | PUTS grid.

Renders one expiry at a time from an `OptionChain` pushed in via `set_chain()` — no network
here (the `OptionsService` owns fetching). A horizontal expiry tab strip (Deribit-style) picks
the active expiry. Two column sets via a toggle: "Chain" (LTP/Theor/Spread/Bid%/Ask%/Distance/
Rel dist/Ann%/Volume) and "Greeks" (Δ/Γ/Θ/V). The bid family renders green and the ask family
red (Deribit-style); volume cells get a blue (calls) / red (puts) magnitude bar; ITM cells are
hatch-shaded; an ATM marker row shows the underlying price mid-table. Errors go to a status
label, never a modal.
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
# layering cues (ITM shading, ATM band, strike spine all rendered the same tone as the table).
# These reintroduce them from the live four-tone surface scale (BG < SURFACE < HOVER < BORDER):
_CELL = theme.TEXT       # bright cell value, like TradingView
_ITM = theme.BORDER      # diagonal-hatch tone for in-the-money cells (visible over SURFACE)
_SPINE = theme.HOVER     # subtly raised centre Strike column (the spine)
_BAND = theme.HOVER      # ATM spot-price marker band
_GREEN = {"bid", "bidpct"}   # Deribit-style: bid family rendered in UP green
_RED = {"ask", "askpct"}     # ask family rendered in DOWN red
_DERIBIT_UNDERLYINGS = ["BTC", "ETH", "SOL"]                          # Deribit crypto options (fixed)
_STOCK_UNDERLYINGS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA"]   # yfinance stock options (editable)


class _GridDelegate(QtWidgets.QStyledItemDelegate):
    """Paints the options grid: volume magnitude bars (blue calls / red puts), the cell text in
    its model foreground colour, the centre Strike-spine vertical dividers, and the horizontal
    price line on the ATM row.

    Drawing the text ourselves is deliberate: the global ``QTableWidget::item { color: ... }`` QSS
    rule overrides per-item ``setForeground`` at paint time, so green-bid / red-ask never showed.
    We blank the style's text and draw the glyph in the model colour to honour it.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.call_col = -1
        self.put_col = -1
        self.spine_left = -1     # Strike column — left edge of the centre spine
        self.spine_right = -1    # IV column — right edge of the centre spine
        self.atm_row = -1        # ATM marker row — gets the full-width price line

    def paint(self, painter, option, index) -> None:
        # Paint everything ourselves (no super().paint): the global QStyleSheetStyle draws the
        # model's display text in its ::item colour regardless of opt.text, which ghosted behind
        # our coloured glyph. So we fill bg/selection, then draw the text in the model colour.
        col, row = index.column(), index.row()
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        r = opt.rect
        painter.save()
        if opt.state & (QtWidgets.QStyle.State_Selected | QtWidgets.QStyle.State_MouseOver):
            painter.fillRect(r, QtGui.QColor(theme.HOVER))
        elif opt.backgroundBrush.style() != QtCore.Qt.NoBrush:
            painter.fillRect(r, opt.backgroundBrush)   # ITM hatch / strike spine / ATM band
        painter.restore()

        if col in (self.call_col, self.put_col):   # volume magnitude bar
            frac = index.data(QtCore.Qt.UserRole) or 0.0
            if frac > 0:
                painter.save()
                bar = QtGui.QColor(_CALL_BAR if col == self.call_col else _PUT_BAR)
                bar.setAlpha(120)
                painter.fillRect(r.x() + 4, r.bottom() - 6,
                                 int((r.width() - 8) * min(frac, 1.0)), 3, bar)
                painter.restore()

        if opt.text:                               # cell text in its model foreground colour
            fg = index.data(QtCore.Qt.ForegroundRole)
            color = fg.color() if isinstance(fg, QtGui.QBrush) else QtGui.QColor(_CELL)
            painter.save()
            painter.setFont(opt.font)
            painter.setPen(color)
            align = int(opt.displayAlignment) or int(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
            painter.drawText(r.adjusted(8, 0, -8, 0), align, opt.text)
            painter.restore()

        if col in (self.spine_left, self.spine_right):   # vertical Strike-spine dividers
            painter.save()
            painter.setPen(QtGui.QColor(theme.BORDER))
            x = r.left() if col == self.spine_left else r.right() - 1
            painter.drawLine(x, r.top(), x, r.bottom())
            painter.restore()

        if row == self.atm_row:                    # horizontal price line across the ATM marker row
            painter.save()
            pen = QtGui.QPen(QtGui.QColor(theme.ACCENT))
            pen.setWidth(2)
            painter.setPen(pen)
            painter.drawLine(r.left(), r.top() + 1, r.right(), r.top() + 1)
            painter.restore()


def _columns(view: str) -> tuple[list[str], list[str], list[str]]:
    """(call_fields outer->centre, put_fields centre->outer, header labels) for a view."""
    side = C.CHAIN_FIELDS if view == "chain" else C.GREEKS_FIELDS
    call_fields = list(reversed(side))
    put_fields = list(side)
    headers = ([C.HEADERS[f] for f in call_fields] + ["Strike", "IV"]
               + [C.HEADERS[f] for f in put_fields])
    return call_fields, put_fields, headers


class _ExpiryStrip(QtWidgets.QScrollArea):
    """Deribit-style horizontal strip of expiry pills (single-select).

    Emits ``selected(iso)`` when the user clicks a pill, and once on ``set_expiries`` for the
    nearest expiry so the owner can load it. Scrolls horizontally when the expiries overflow.
    """

    selected = QtCore.Signal(str)   # expiry ISO date

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFixedHeight(46)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}"
            f"QToolButton{{background:{theme.SURFACE};color:{theme.TEXT2};"
            f"border:1px solid {theme.BORDER};border-radius:{theme.RADIUS_MD}px;"
            "padding:6px 13px;font-size:13px;}"
            f"QToolButton:hover{{color:{theme.TEXT};border-color:{theme.TEXT3};}}"
            f"QToolButton:checked{{background:{theme.HOVER};color:{theme.TEXT};"
            f"border-color:{theme.ACCENT};}}")
        body = QtWidgets.QWidget()
        self._row = QtWidgets.QHBoxLayout(body)
        self._row.setContentsMargins(0, 4, 0, 4)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self.setWidget(body)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QtWidgets.QToolButton] = {}

    def set_expiries(self, expiries) -> None:
        """Rebuild the pills; auto-select the first (nearest) expiry and announce it."""
        for b in self._buttons.values():
            self._group.removeButton(b)
            b.deleteLater()
        self._buttons.clear()
        for e in expiries:
            b = QtWidgets.QToolButton()
            b.setText(e.label)
            b.setToolTip(f"{e.label} · {e.dte}DTE · {e.date}")
            b.setCheckable(True)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _checked=False, iso=e.date: self.selected.emit(iso))
            self._group.addButton(b)
            self._row.insertWidget(self._row.count() - 1, b)   # before the trailing stretch
            self._buttons[e.date] = b
        if expiries:
            self._buttons[expiries[0].date].setChecked(True)
            self.selected.emit(expiries[0].date)

    def current(self) -> str | None:
        return next((iso for iso, b in self._buttons.items() if b.isChecked()), None)


class OptionsTab(QtWidgets.QWidget):
    """The Options space (full-width central tab)."""

    underlyingChanged = QtCore.Signal(str)
    expiryChanged = QtCore.Signal(str)       # expiry ISO date
    rangeChanged = QtCore.Signal()           # expiration-range filter changed
    refreshRequested = QtCore.Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._chain: OptionChain | None = None
        self._col_field: dict[int, tuple[str, str | None]] = {}  # column -> (field, side)
        self._sort: tuple | None = None                  # None=strike order; else (field, side, desc)
        self._view = "chain"
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        controls = QtWidgets.QHBoxLayout()
        controls.setSpacing(8)
        self.provider = QtWidgets.QComboBox()
        self.provider.addItems(["Deribit", "yfinance"])   # crypto (Deribit) | stock options (yfinance)
        self.underlying = QtWidgets.QComboBox()            # populated per provider by _apply_provider
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
        for w in (self.provider, self.underlying, self.exp_range, self.strikes,
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

        # Deribit-style expiry tab strip: picks the single active expiry shown below.
        self.expiry_strip = _ExpiryStrip()
        root.addWidget(self.expiry_strip)

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
        self._bar = _GridDelegate(self.table)
        self.table.setItemDelegate(self._bar)
        root.addWidget(self.table, 1)

        # Provider scopes the underlying list; picking a preset fires `activated`; for the editable
        # (yfinance) combo, typing a custom ticker + Enter fires the line-edit's `returnPressed`.
        self.provider.activated.connect(lambda _i: self._apply_provider(emit=True))
        self.underlying.activated.connect(self._emit_underlying)
        self.expiry_strip.selected.connect(self.expiryChanged)   # strip drives the single expiry
        self.strikes.activated.connect(self.refreshRequested.emit)
        self.exp_range.activated.connect(self.rangeChanged.emit)
        self.view_toggle.activated.connect(self._on_view_changed)
        self.refresh_btn.clicked.connect(self.refreshRequested.emit)
        self.table.horizontalHeader().sectionClicked.connect(self._on_header_clicked)
        self._apply_provider(emit=False)   # populate the underlying list for the default provider

    # --- signals out ---------------------------------------------------------
    def _emit_underlying(self) -> None:
        self.underlyingChanged.emit(self.underlying.currentText())

    def _apply_provider(self, *, emit: bool = True) -> None:
        """Scope the underlying list to the chosen provider: Deribit -> BTC/ETH/SOL (fixed);
        yfinance -> editable stock presets. Emits underlyingChanged only on a user-driven switch
        (emit=False at construction keeps startup network-free)."""
        deribit = self.provider.currentText() == "Deribit"
        self.underlying.blockSignals(True)
        self.underlying.clear()
        self.underlying.setEditable(not deribit)   # Deribit: fixed coins; yfinance: type any ticker
        self.underlying.addItems(_DERIBIT_UNDERLYINGS if deribit else _STOCK_UNDERLYINGS)
        self.underlying.setCurrentIndex(0)
        self.underlying.blockSignals(False)
        if not deribit and self.underlying.lineEdit() is not None:
            try:
                self.underlying.lineEdit().returnPressed.disconnect()
            except (RuntimeError, TypeError):
                pass
            self.underlying.lineEdit().returnPressed.connect(self._emit_underlying)
        if emit:
            self._emit_underlying()

    def _on_view_changed(self) -> None:
        self._view = "greeks" if self.view_toggle.currentText() == "Greeks" else "chain"
        self._render()  # redraw with the new column set

    # --- inputs --------------------------------------------------------------
    def strikes_value(self) -> int | None:
        return self.strikes.currentData()       # data carries the int window (6/12) or None=All

    def exp_range_days(self) -> int | None:
        """Max DTE to list as expiry pills, or None for all expiries."""
        return {"Next 30d": 30, "Next 60d": 60, "Next 90d": 90, "All": None}[
            self.exp_range.currentText()]

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_expiries(self, expiries) -> None:
        """Populate the expiry tab strip; selecting the nearest expiry fires expiryChanged."""
        self.expiry_strip.set_expiries(expiries)

    @staticmethod
    def _source_note(source: str) -> str:
        """Flag the free fallback so the data source (and how to upgrade) is always clear."""
        if source == "yfinance":
            return "  ·  free feed — greeks inferred (set options_stock_provider for exchange-grade)"
        return ""

    def set_chain(self, chain: OptionChain) -> None:
        """Show one expiry's flat chain grid."""
        self._chain = chain
        self._render()
        px = "—" if chain.underlying_price is None else f"{chain.underlying_price:,.2f}"
        self.set_status(
            f"{chain.underlying} {px}  ·  {chain.source}  ·  {chain.expiry.label}"
            + self._source_note(chain.source))

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
        self._bar.spine_left = strike_col          # Strike col — left edge of the centre spine
        self._bar.spine_right = strike_col + 1      # IV col — right edge of the centre spine
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
        self._render()

    def _render(self) -> None:
        self._bar.atm_row = -1
        call_fields, put_fields, ncols, strike_col = self._setup_columns()
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
            self._bar.atm_row = pos

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
            elif raw is not None and field in _GREEN:
                item.setForeground(QtGui.QColor(theme.UP))     # bid family — green (Deribit-style)
            elif raw is not None and field in _RED:
                item.setForeground(QtGui.QColor(theme.DOWN))   # ask family — red (Deribit-style)
            else:
                item.setForeground(QtGui.QColor(_CELL))   # bright like TV, not dim TEXT2
            if itm:
                hatch = QtGui.QColor(_ITM)
                hatch.setAlpha(150)   # soften the diagonal hatch so ITM reads subtly, not busy
                item.setBackground(QtGui.QBrush(hatch, QtCore.Qt.BDiagPattern))
            self.table.setItem(ri, base + i, item)

    @staticmethod
    def _fmt_strike(strike: float) -> str:
        """Strike label: drop the trailing ".00" on whole strikes (BTC 64,000) while keeping
        fractional ones (VIX 14.5) — matching how Deribit/TradingView print strikes."""
        s = f"{strike:,.2f}"
        return s.rstrip("0").rstrip(".") if "." in s else s

    def _strike_iv(self, ri: int, row: StrikeRow, strike_col: int) -> None:
        strike = QtWidgets.QTableWidgetItem(self._fmt_strike(row.strike))
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
        return pos
