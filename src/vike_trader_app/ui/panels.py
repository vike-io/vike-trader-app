"""Side panels: backtest report (stat cards + overfit verdict), trades table,
watchlist, strategy params, and run history — styled in the vike.io look.
"""

from datetime import UTC, datetime

from PySide6 import QtCore, QtWidgets

from ..analysis import metrics
from . import icons, theme
from .linkbus import LINK_COLOR, LINK_GROUPS
from .tables import TRADE_HEADERS, trade_rows


class LinkDot(QtWidgets.QToolButton):
    """A small colour-swatch button for picking a symbol link group (MultiCharts style).

    Shows a filled dot in the current group's colour (hollow grey for the unlinked group 0);
    clicking pops a menu of colours and emits ``groupChanged(int)`` on selection.
    """

    groupChanged = QtCore.Signal(int)

    def __init__(self, group: int = 0, parent=None):
        super().__init__(parent)
        self._group = group
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedSize(22, 22)
        self.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(self)
        for gid, _color, name in LINK_GROUPS:
            act = menu.addAction(name)
            act.triggered.connect(lambda _c=False, g=gid: self.set_group(g, emit=True))
        self.setMenu(menu)
        self._refresh()

    def group(self) -> int:
        return self._group

    def set_group(self, gid: int, *, emit: bool = False) -> None:
        self._group = gid
        self._refresh()
        if emit:
            self.groupChanged.emit(gid)

    def _refresh(self) -> None:
        color = LINK_COLOR.get(self._group, LINK_COLOR[0])
        self.setText("○" if self._group == 0 else "●")
        self.setToolTip("Symbol link: " + next(
            (n for g, _c, n in LINK_GROUPS if g == self._group), "None"))
        self.setStyleSheet(
            f"QToolButton{{border:none;background:transparent;color:{color};font-size:15px;}}"
            f"QToolButton::menu-indicator{{image:none;width:0;}}"
        )


class TablePlaceholder(QtCore.QObject):
    """Dim, centred empty-state label overlaid on a results table.

    Parented to the table's viewport so it floats over the (otherwise blank) grid; it stays centred
    on resize via an event filter and is shown only when the table has 0 rows. Call ``sync()`` from
    the table's populate/refresh path so it toggles automatically.
    """

    def __init__(self, table: QtWidgets.QTableView, text: str):
        super().__init__(table)
        self._table = table
        self._label = QtWidgets.QLabel(text, table.viewport())
        self._label.setAlignment(QtCore.Qt.AlignCenter)
        self._label.setStyleSheet(f"color:{theme.TEXT3};font-size:13px;background:transparent;")
        self._label.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents)
        table.viewport().installEventFilter(self)
        self._reposition()
        self.sync()

    def eventFilter(self, obj, event):  # noqa: N802 - keep the overlay centred as the viewport resizes
        # The filter is installed only on the table's viewport, so any Resize here is ours. Don't
        # re-fetch self._table.viewport() (it raises if the C++ table was deleted during teardown).
        if event.type() == QtCore.QEvent.Resize:
            self._reposition()
        return False

    def _reposition(self) -> None:
        try:
            self._label.setGeometry(self._table.viewport().rect())
        except RuntimeError:
            pass  # table/viewport torn down (C++ object already deleted)

    def sync(self) -> None:
        """Show the placeholder only when the table is empty."""
        try:
            model = self._table.model()
            rows = model.rowCount() if model is not None else 0
            self._label.setVisible(rows == 0)
        except RuntimeError:
            pass  # table torn down


def _card(label: str) -> tuple[QtWidgets.QFrame, QtWidgets.QLabel]:
    """A small stat card: dim uppercase label over a big value. Returns (card, value)."""
    card = QtWidgets.QFrame()
    card.setStyleSheet(
        f"QFrame{{background:{theme.PANEL2};border:1px solid {theme.BORDER};border-radius:8px;}}"
    )
    lay = QtWidgets.QVBoxLayout(card)
    lay.setContentsMargins(11, 9, 11, 9)
    lay.setSpacing(3)
    lbl = QtWidgets.QLabel(label.upper())
    lbl.setStyleSheet(f"color:{theme.TEXT3};font-size:9px;letter-spacing:.6px;border:none;")
    val = QtWidgets.QLabel("—")
    val.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:600;border:none;")
    lay.addWidget(lbl)
    lay.addWidget(val)
    return card, val


def strategy_params(cls) -> dict:
    """Public, numeric class-attribute params of a strategy (e.g. fast/slow)."""
    return {
        k: v
        for k, v in vars(cls).items()
        if not k.startswith("_") and isinstance(v, int | float) and not isinstance(v, bool)
    }


class ReportPanel(QtWidgets.QWidget):
    """Overfit-verdict banner (hidden until validated) + a grid of stat cards."""

    def __init__(self):
        super().__init__()
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(9, 9, 9, 9)
        root.setSpacing(9)

        # verdict banner
        self.verdict = QtWidgets.QFrame()
        self.verdict.setVisible(False)
        vlay = QtWidgets.QVBoxLayout(self.verdict)
        vlay.setContentsMargins(12, 10, 12, 10)
        vlay.setSpacing(6)
        self.verdict_title = QtWidgets.QLabel("⚠ OVERFIT RISK")
        self.verdict_title.setStyleSheet("font-size:13px;font-weight:700;border:none;")
        self.verdict_sub = QtWidgets.QLabel("")
        self.verdict_sub.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;border:none;")
        self.verdict_sub.setWordWrap(True)
        vlay.addWidget(self.verdict_title)
        vlay.addWidget(self.verdict_sub)
        root.addWidget(self.verdict)

        # stat-card grid
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(7)
        self._fields = ["return", "equity", "trades", "winrate", "pf", "mdd", "sharpe"]
        labels = {
            "return": "Net return",
            "equity": "Final equity",
            "trades": "Trades",
            "winrate": "Win rate",
            "pf": "Profit factor",
            "mdd": "Max drawdown",
            "sharpe": "Sharpe (ann.)",
        }
        self._vals: dict[str, QtWidgets.QLabel] = {}
        # Single column: the report dock is narrow (~260px), so a 2-column grid overflowed and
        # clipped the right column ("Final equity", "Win rate", "Max drawdown"). One column per
        # row always fits the dock width at any size.
        order = ["return", "equity", "trades", "winrate", "pf", "mdd", "sharpe"]
        for i, key in enumerate(order):
            card, val = _card(labels[key])
            self._vals[key] = val
            grid.addWidget(card, i, 0)
        root.addLayout(grid)
        root.addStretch(1)

    def update_stats(self, result):
        eq = result.equity_curve
        ret = metrics.total_return(eq) * 100
        self._set("return", f"{ret:+.2f}%", theme.UP if ret >= 0 else theme.DOWN)
        self._set("equity", f"${result.final_equity:,.2f}")
        self._set("trades", str(len(result.trades)))
        self._set("winrate", f"{metrics.win_rate(result.trades) * 100:.1f}%")
        pf = metrics.profit_factor(result.trades)
        self._set("pf", "∞" if pf == float("inf") else f"{pf:.2f}")
        self._set("mdd", f"-{metrics.max_drawdown(eq) * 100:.2f}%", theme.DOWN)
        s = metrics.sharpe(eq)
        self._set("sharpe", f"{s:.2f}", theme.UP if s >= 0 else theme.DOWN)

    def _set(self, key, text, color=None):
        self._vals[key].setText(text)
        c = color or theme.TEXT
        self._vals[key].setStyleSheet(f"color:{c};font-size:18px;font-weight:600;border:none;")

    def show_verdict(self, report):
        """Populate the verdict banner from an analysis.report.OverfitReport."""
        level = report.verdict.level
        color = theme.VERDICT.get(level, theme.TEXT2)
        self.verdict.setStyleSheet(
            f"QFrame{{background:rgba(255,176,0,0.08);border:1px solid {color};border-radius:8px;}}"
        )
        self.verdict_title.setText(f"⚠ OVERFIT RISK · {level.upper()}")
        self.verdict_title.setStyleSheet(
            f"color:{color};font-size:13px;font-weight:700;border:none;"
        )
        self.verdict_sub.setText(
            f"PBO {report.pbo:.0%}  ·  Deflated Sharpe {report.deflated_sharpe:.0%}  "
            f"·  {report.n_trials} configs\n" + "  ".join(f"• {r}" for r in report.verdict.reasons)
        )
        self.verdict.setVisible(True)


class TradesTable(QtWidgets.QTableWidget):
    """Read-only table of completed trades."""

    def __init__(self):
        super().__init__(0, len(TRADE_HEADERS) + 1)  # +1: trailing spacer column
        self.setHorizontalHeaderLabels([*TRADE_HEADERS, ""])
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        # Slack goes to an empty trailing spacer, NOT the last data column — stretching
        # right-aligned "Fees" ballooned it and pinned its values to the table edge.
        hdr = self.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(len(TRADE_HEADERS), QtWidgets.QHeaderView.Stretch)
        hdr.setDefaultAlignment(QtCore.Qt.AlignRight)

    def update_trades(self, trades):
        rows = trade_rows(trades)
        self.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                item = QtWidgets.QTableWidgetItem(val)
                item.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
                if c == 6:  # PnL column: colour by sign
                    item.setForeground(QtCore.Qt.green if val.startswith("+") else QtCore.Qt.red)
                self.setItem(r, c, item)


class WatchlistPanel(QtWidgets.QListWidget):
    """Clickable symbol list, grouped by asset class. Emits ``symbolChosen(symbol)``.

    Populated from the local cache (``set_symbols``) so every clickable row maps to data
    that loads instantly. Falls back to a small demo list until ``set_symbols`` is called.
    (No live prices — the cache is historical; a right-hand ``1m`` chip marks the cached
    resolution. Reading every file for a price column would block startup, so it's omitted.)
    """

    symbolChosen = QtCore.Signal(str)
    openInNewChart = QtCore.Signal(str)   # right-click → open the symbol as a new chart document

    _DEMO = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    # instrument-badge colours (asset-free flag stand-ins)
    _COIN_COLOR = {
        "BTC": "#f7931a", "ETH": "#627eea", "SOL": "#14f195",
        "DOGE": "#c2a633", "AVAX": "#e84142", "TON": "#0098ea",
    }
    _CCY_COLOR = {
        "USD": "#3fb950", "EUR": "#58a6ff", "GBP": "#a855f7", "JPY": "#f85149",
        "CHF": "#f0883e", "AUD": "#26c6da", "CAD": "#ec407a", "NZD": "#66bb6a",
        "SGD": "#ffb000", "HKD": "#f85149", "MXN": "#3fb950", "ZAR": "#ffb000",
        "TRY": "#f85149", "SEK": "#58a6ff", "NOK": "#58a6ff", "PLN": "#f85149",
    }
    _CCY_SYM = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}

    def __init__(self):
        super().__init__()
        self._price_labels: dict[str, QtWidgets.QLabel] = {}  # symbol -> price label
        self._chg_labels: dict[str, QtWidgets.QLabel] = {}    # symbol -> change% label
        self.itemActivated.connect(self._chosen)
        self.itemClicked.connect(self._chosen)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        for sym in self._DEMO:
            self._add_row(sym)
        if self.count():
            self.setCurrentRow(0)

    # --- public population API ---

    def set_symbols(self, groups: list[tuple[str, list[str]]]) -> None:
        """Rebuild the list from ``[(group_name, [symbols]), ...]`` with section headers."""
        self.clear()
        self._price_labels = {}  # repopulated by _add_row below
        self._chg_labels = {}
        first: QtWidgets.QListWidgetItem | None = None
        for gname, syms in groups:
            if not syms:
                continue
            self._add_header(gname)
            for sym in syms:
                item = self._add_row(sym)
                if first is None:
                    first = item
        if first is not None:
            self.setCurrentItem(first)

    # --- internals ---

    @staticmethod
    def _pair_label(sym: str) -> tuple[str, str]:
        if sym.endswith("USDT"):
            return sym[:-4], "/USDT"
        if len(sym) == 6 and sym.isalpha():       # forex like EURUSD -> EUR /USD
            return sym[:3], "/" + sym[3:]
        return sym, ""

    @staticmethod
    def _is_forex(sym: str) -> bool:
        s = sym.upper()
        return len(s) == 6 and s.isalpha()

    @classmethod
    def _badge(cls, sym: str) -> tuple[str, str]:
        """(label, colour) for the instrument badge — coin initial / currency symbol."""
        if sym.endswith("USDT"):
            base = sym[:-4]
            return base[:1], cls._COIN_COLOR.get(base, theme.ACCENT)
        if len(sym) == 6 and sym.isalpha():
            base = sym[:3]
            return cls._CCY_SYM.get(base, base[:1]), cls._CCY_COLOR.get(base, theme.BLUE)
        return sym[:1], theme.TEXT3

    def set_prices(self, prices: dict) -> None:
        """Fill each row from ``{symbol: (last_close, change_frac)}``.

        Crypto rows show the last price + 24h change%. Forex rows show bid/ask (mid ∓ ~1 pip)
        + change% — the historical cache has no live quote/spread, so the spread is a nominal
        display value, not a live quote. Called from a main-thread reader (startup-safe).
        """
        for sym, val in prices.items():
            close, chg = val if isinstance(val, tuple) else (val, None)
            pl = self._price_labels.get(sym)
            cl = self._chg_labels.get(sym)
            if pl is None or close is None:
                continue
            if self._is_forex(sym):
                pip = 0.01 if sym.upper().endswith("JPY") else 0.0001
                dp = 3 if pip == 0.01 else 5
                bid, ask = close - pip, close + pip
                pl.setText(
                    f"<span style='color:{theme.DOWN}'>{bid:.{dp}f}</span>"
                    f"<span style='color:{theme.TEXT3}'> / </span>"
                    f"<span style='color:{theme.UP}'>{ask:.{dp}f}</span>"
                )
            else:
                pl.setText(f"{close:,.2f}")
            if cl is not None and chg is not None:
                col = theme.UP if chg >= 0 else theme.DOWN
                cl.setText(f"<span style='color:{col}'>{chg * 100:+.2f}%</span>")

    def _add_header(self, text: str) -> None:
        item = QtWidgets.QListWidgetItem(self)
        item.setFlags(QtCore.Qt.NoItemFlags)       # non-selectable, non-clickable divider
        lbl = QtWidgets.QLabel(text.upper())
        lbl.setStyleSheet(
            f"color:{theme.TEXT3};font-size:9px;font-weight:700;letter-spacing:1px;"
            f"border:none;padding:6px 11px 2px 11px;"
        )
        item.setSizeHint(lbl.sizeHint())
        self.setItemWidget(item, lbl)

    def _add_row(self, sym: str) -> QtWidgets.QListWidgetItem:
        item = QtWidgets.QListWidgetItem(self)
        item.setData(QtCore.Qt.UserRole, sym)
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(10, 5, 11, 5)
        lay.setSpacing(8)

        # instrument badge (coin initial / currency symbol)
        text, color = self._badge(sym)
        badge = QtWidgets.QLabel()
        badge.setPixmap(icons.avatar(text, color).scaled(
            18, 18, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
        badge.setFixedWidth(20)

        base, quote = self._pair_label(sym)
        name = QtWidgets.QLabel(
            f"{base}<span style='color:{theme.TEXT3};font-size:9px'>{quote}</span>"
        )
        name.setStyleSheet(f"color:{theme.TEXT};font-size:14px;font-weight:600;border:none;")

        # right column: price/bid-ask (top) + change% (bottom), right-aligned
        vbox = QtWidgets.QVBoxLayout()
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)
        price = QtWidgets.QLabel("")
        price.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        price.setStyleSheet(
            f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-size:10px;border:none;"
        )
        chg = QtWidgets.QLabel("")
        chg.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        chg.setStyleSheet(
            f"color:{theme.TEXT3};font-family:{theme.FONT_MONO};font-size:9px;border:none;"
        )
        vbox.addWidget(price)
        vbox.addWidget(chg)
        self._price_labels[sym] = price
        self._chg_labels[sym] = chg

        lay.addWidget(badge)
        lay.addWidget(name)
        lay.addStretch(1)
        lay.addLayout(vbox)
        item.setSizeHint(w.sizeHint())
        self.setItemWidget(item, w)
        return item

    def _chosen(self, item):
        sym = item.data(QtCore.Qt.UserRole)
        if sym:                                    # ignore clicks on group-header rows
            self.symbolChosen.emit(sym)

    def _show_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        sym = item.data(QtCore.Qt.UserRole) if item is not None else None
        if not sym:                                # header row or empty space
            return
        menu = QtWidgets.QMenu(self)
        act_open = menu.addAction("Open")
        act_new = menu.addAction("Open in new chart")
        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self.symbolChosen.emit(sym)
        elif chosen is act_new:
            self.openInNewChart.emit(sym)


class StrategyPanel(QtWidgets.QWidget):
    """Shows the active strategy name + its parameters (from class attributes)."""

    def __init__(self):
        super().__init__()
        self._lay = QtWidgets.QVBoxLayout(self)
        self._lay.setContentsMargins(11, 11, 11, 11)
        self._lay.setSpacing(9)
        self._name = QtWidgets.QLabel("—")
        self._name.setStyleSheet("font-size:14px;font-weight:700;border:none;")
        self._lay.addWidget(self._name)
        self._grid = QtWidgets.QGridLayout()
        self._grid.setSpacing(7)
        self._lay.addLayout(self._grid)
        self._lay.addStretch(1)

    def show_strategy(self, cls):
        self._name.setText(cls.__name__)
        while self._grid.count():
            self._grid.takeAt(0).widget().deleteLater()
        params = strategy_params(cls) or {"params": 0}
        for i, (k, v) in enumerate(params.items()):
            card, val = _card(k)
            val.setText(str(v))
            self._grid.addWidget(card, i // 2, i % 2)


class HistoryPanel(QtWidgets.QTableWidget):
    """Persistent log of past backtest runs. Double-click a row to reopen it."""

    HEADERS = ["Time", "Symbol", "TF", "Strategy", "Return", "Sharpe", "Trades"]
    runChosen = QtCore.Signal(object)

    def __init__(self):
        super().__init__(0, len(self.HEADERS))
        self.setHorizontalHeaderLabels(self.HEADERS)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        # Strategy (free text) absorbs the slack — stretching the last numeric column
        # ("Trades") ballooned it (the calendar bug class).
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self.itemDoubleClicked.connect(self._double_clicked)

    def update_runs(self, runs):
        self.setRowCount(len(runs))
        for r, rec in enumerate(runs):
            when = datetime.fromtimestamp(rec.ts / 1000, UTC).strftime("%m-%d %H:%M")
            cells = [
                when,
                rec.symbol,
                rec.interval,
                rec.strategy,
                f"{rec.net_return * 100:+.2f}%",
                f"{rec.sharpe:.2f}",
                str(rec.trades),
            ]
            for c, val in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(val)
                if c == 4:  # return column: colour by sign
                    item.setForeground(QtCore.Qt.green if rec.net_return >= 0 else QtCore.Qt.red)
                self.setItem(r, c, item)
            self.item(r, 0).setData(QtCore.Qt.UserRole, rec)

    def _double_clicked(self, item):
        rec = self.item(item.row(), 0).data(QtCore.Qt.UserRole)
        if rec is not None:
            self.runChosen.emit(rec)
