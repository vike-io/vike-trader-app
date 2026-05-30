"""Side panels: backtest report (stat cards + overfit verdict), trades table,
watchlist, strategy params, and run history — styled in the vike.io look.
"""

from datetime import UTC, datetime

from PySide6 import QtCore, QtWidgets

from ..analysis import metrics
from . import theme
from .tables import TRADE_HEADERS, trade_rows


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
        order = ["return", "equity", "trades", "winrate", "pf", "mdd"]
        for i, key in enumerate(order):
            card, val = _card(labels[key])
            self._vals[key] = val
            grid.addWidget(card, i // 2, i % 2)
        card, val = _card(labels["sharpe"])  # full-width
        self._vals["sharpe"] = val
        grid.addWidget(card, 3, 0, 1, 2)
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
        super().__init__(0, len(TRADE_HEADERS))
        self.setHorizontalHeaderLabels(TRADE_HEADERS)
        self.verticalHeader().setVisible(False)
        self.setAlternatingRowColors(True)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignRight)

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
    """Clickable crypto symbol list. Emits ``symbolChosen(symbol)`` on activation."""

    symbolChosen = QtCore.Signal(str)

    _DEMO = [
        ("BTCUSDT", "72,955.40", 1.82),
        ("ETHUSDT", "3,704.18", 0.94),
        ("SOLUSDT", "168.92", -2.11),
        ("BNBUSDT", "604.50", 0.37),
        ("XRPUSDT", "0.5218", -1.04),
        ("DOGEUSDT", "0.1402", 3.66),
        ("ADAUSDT", "0.4471", -0.58),
        ("AVAXUSDT", "34.07", 2.20),
        ("LINKUSDT", "17.83", 0.12),
        ("TONUSDT", "7.41", -0.83),
    ]

    def __init__(self):
        super().__init__()
        self.itemActivated.connect(self._chosen)
        self.itemClicked.connect(self._chosen)
        for sym, px, chg in self._DEMO:
            self._add_row(sym, px, chg)
        if self.count():
            self.setCurrentRow(0)

    def _add_row(self, sym, px, chg):
        item = QtWidgets.QListWidgetItem(self)
        item.setData(QtCore.Qt.UserRole, sym)
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(11, 6, 11, 6)
        base = sym[:-4] if sym.endswith("USDT") else sym
        name = QtWidgets.QLabel(
            f"{base}<span style='color:{theme.TEXT3};font-size:9px'>/USDT</span>"
        )
        name.setStyleSheet(f"color:{theme.TEXT};font-weight:600;border:none;")
        col = theme.UP if chg >= 0 else theme.DOWN
        right = QtWidgets.QLabel(
            f"<div style='text-align:right'>{px}<br>"
            f"<span style='color:{col};font-size:10px'>{chg:+.2f}%</span></div>"
        )
        right.setStyleSheet("border:none;")
        right.setAlignment(QtCore.Qt.AlignRight)
        lay.addWidget(name)
        lay.addStretch(1)
        lay.addWidget(right)
        item.setSizeHint(w.sizeHint())
        self.setItemWidget(item, w)

    def _chosen(self, item):
        self.symbolChosen.emit(item.data(QtCore.Qt.UserRole))


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
        self.horizontalHeader().setStretchLastSection(True)
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
