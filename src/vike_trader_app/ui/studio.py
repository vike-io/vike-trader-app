"""Studio tab: ChatPanel | CodeEditor | ResultsPanel with Run wiring and ChatWorker thread.

The ResultsPanel has five tabs — Equity (stand-alone equity curve), Performance (KPI hero
tiles + detail grid), Trades (round-trips), Runs (iterate-and-compare history), and
Distribution (trade-return histogram). The price candlestick chart now lives in the Chart
space (app.py), not here. The overfit-risk verdict banner sits above the tabs.
"""

import difflib
import html

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis import report_extras
from . import theme
from .chart import EquityChart
from .editor import CodeEditor
from .flowlayout import FlowLayout

_YEAR_MS = 365.25 * 24 * 60 * 60 * 1000.0


# ---------------------------------------------------------------------------
# ResultsPanel
# ---------------------------------------------------------------------------

class ResultsPanel(QtWidgets.QWidget):
    """Tabbed results — Equity | Performance | Trades | Runs | Distribution."""

    # hero tiles (caption); values + $ sub-lines are computed in show_report
    _HERO = [
        ("roi",            "ROI"),
        ("annualized",     "Annualized ROI"),
        ("win_ratio",      "Win Ratio"),
        ("max_drawdown",   "Max Drawdown"),
        ("time_in_market", "Time in Market"),
        ("profit_factor",  "Profit Factor"),
    ]
    # detail grid (plain TesterReport attributes)
    _DETAIL = [
        ("sharpe",          "Sharpe"),
        ("sortino",         "Sortino"),
        ("net_profit",      "Net profit"),
        ("expected_payoff", "Expected payoff"),
        ("recovery_factor", "Recovery factor"),
        ("avg_win",         "Avg win"),
        ("avg_loss",        "Avg loss"),
        ("total_fees",      "Total fees"),
    ]
    _SIGNED = {"net_profit", "expected_payoff", "avg_win"}

    _TRADE_COLS = ["#", "Side", "Entry", "Exit", "Size", "PnL", "Return", "MFE", "MAE"]
    _RUN_COLS = ["#", "Return", "Max DD", "Trades", "Sharpe"]

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # verdict banner (above the tabs — always visible when set)
        self._banner = QtWidgets.QLabel()
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        self._banner.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._banner)

        # status / toast line (errors red, success green)
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        self._status.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._status)

        self._tabs = QtWidgets.QTabWidget()
        root.addWidget(self._tabs, 1)

        self._build_equity_tab()
        self._build_performance_tab()
        self._build_trades_tab()
        self._build_runs_tab()
        self._build_distribution_tab()

        self.last_report: object = None
        self._report_trades: list = []           # row -> Trade, for the chart-focus linkage
        self._runs: list = []                     # stored runs: {report, bars, overlays}

    # --- tab builders ---

    def _build_equity_tab(self) -> None:
        self._equity = EquityChart()
        self._tabs.addTab(self._equity, "Equity")

    def _make_tile(self, caption: str):
        cell = QtWidgets.QWidget()
        cell.setAttribute(QtCore.Qt.WA_StyledBackground, True)  # paint bg/border on a bare QWidget
        cell.setStyleSheet(
            f"background:{theme.PANEL2};border:1px solid {theme.BORDER};border-radius:10px;"
        )
        cv = QtWidgets.QVBoxLayout(cell)
        cv.setContentsMargins(14, 11, 14, 11)
        cv.setSpacing(3)
        cap = QtWidgets.QLabel(caption.upper())
        cap.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;border:none;"
        )
        val = QtWidgets.QLabel("—")
        val.setStyleSheet(
            f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-weight:700;"
            f"font-size:23px;border:none;"
        )
        sub = QtWidgets.QLabel("")
        sub.setStyleSheet(
            f"color:{theme.TEXT2};font-family:{theme.FONT_MONO};font-size:11px;border:none;"
        )
        cv.addWidget(cap)
        cv.addWidget(val)
        cv.addWidget(sub)
        return cell, val, sub

    def _build_performance_tab(self) -> None:
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        body = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(body)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(14)

        # hero KPI tiles (2 columns)
        hero = QtWidgets.QGridLayout()
        hero.setHorizontalSpacing(18)
        hero.setVerticalSpacing(12)
        self._hero_val: dict[str, QtWidgets.QLabel] = {}
        self._hero_sub: dict[str, QtWidgets.QLabel] = {}
        for i, (key, label) in enumerate(self._HERO):
            cell, val, sub = self._make_tile(label)
            self._hero_val[key] = val
            self._hero_sub[key] = sub
            hero.addWidget(cell, i // 2, i % 2)
        outer.addLayout(hero)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color:{theme.BORDER};")
        outer.addWidget(line)

        # detail grid (smaller)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        self._value_labels: dict[str, QtWidgets.QLabel] = {}
        for i, (key, label) in enumerate(self._DETAIL):
            cap = QtWidgets.QLabel(label.upper())
            cap.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;")
            val = QtWidgets.QLabel("—")
            val.setStyleSheet(self._metric_style("", None))
            self._value_labels[key] = val
            r, c = divmod(i, 2)
            holder = QtWidgets.QWidget()
            hv = QtWidgets.QVBoxLayout(holder)
            hv.setContentsMargins(0, 0, 0, 0)
            hv.setSpacing(0)
            hv.addWidget(cap)
            hv.addWidget(val)
            grid.addWidget(holder, r, c)
        outer.addLayout(grid)
        outer.addStretch(1)

        scroll.setWidget(body)
        self._tabs.addTab(scroll, "Performance")

    def _build_trades_tab(self) -> None:
        self._trades = QtWidgets.QTableWidget(0, len(self._TRADE_COLS))
        self._trades.setHorizontalHeaderLabels(self._TRADE_COLS)
        self._trades.verticalHeader().setVisible(False)
        self._trades.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._trades.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._trades.setAlternatingRowColors(True)
        hdr = self._trades.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self._trades.cellClicked.connect(self._on_trade_clicked)
        self._tabs.addTab(self._trades, "Trades")

    def _build_runs_tab(self) -> None:
        self._runs_table = QtWidgets.QTableWidget(0, len(self._RUN_COLS))
        self._runs_table.setHorizontalHeaderLabels(self._RUN_COLS)
        self._runs_table.verticalHeader().setVisible(False)
        self._runs_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._runs_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._runs_table.setAlternatingRowColors(True)
        self._runs_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self._runs_table.cellClicked.connect(self._on_run_clicked)
        self._tabs.addTab(self._runs_table, "Runs")

    def _build_distribution_tab(self) -> None:
        self._dist = pg.PlotWidget()
        self._dist.setBackground(theme.BG)
        self._dist.showGrid(x=True, y=True, alpha=0.12)
        self._dist.getAxis("left").setTextPen(theme.TEXT3)
        self._dist.getAxis("bottom").setTextPen(theme.TEXT3)
        self._dist.getAxis("bottom").enableAutoSIPrefix(False)  # show raw return fractions
        self._dist.setLabel("bottom", "trade return")
        self._dist.setLabel("left", "count")
        self._tabs.addTab(self._dist, "Distribution")

    def mount_chart_tab(self, chart: QtWidgets.QWidget) -> None:
        """Add the price chart as a 'Chart' tab after Distribution, so the reports and the chart
        share one top tab strip (Equity | Performance | Trades | Runs | Distribution | Chart)."""
        self._tabs.addTab(chart, "Chart")

    def _update_distribution(self, returns) -> None:
        self._dist.clear()
        edges, counts = report_extras.returns_histogram(returns, bins=20)
        if not counts:
            return
        width = (edges[1] - edges[0]) * 0.9
        for i, h in enumerate(counts):
            cx = (edges[i] + edges[i + 1]) / 2.0
            color = theme.UP if cx >= 0 else theme.DOWN
            self._dist.addItem(pg.BarGraphItem(x=[cx], height=[h], width=width,
                                               brush=pg.mkBrush(color), pen=None))
        self._dist.addLine(x=0.0, pen=pg.mkPen(theme.TEXT3, style=QtCore.Qt.DashLine))

    # --- formatting helpers ---

    @staticmethod
    def _pct(v) -> str:
        if v is None or v != v:            # None or NaN
            return "—"
        if v in (float("inf"), float("-inf")):
            return "∞" if v > 0 else "−∞"
        return f"{v * 100:.2f}%"

    @staticmethod
    def _money(v) -> str:
        if v is None or v != v or v in (float("inf"), float("-inf")):
            return ""
        sign = "+" if v > 0 else "−" if v < 0 else ""
        return f"{sign}${abs(v):,.2f}"

    def _fmt(self, key: str, raw) -> str:
        if raw is None:
            return "—"
        if isinstance(raw, int):
            return str(raw)
        if raw == float("inf"):
            return "∞"
        if raw == float("-inf"):
            return "−∞"
        return f"{raw:,.2f}"

    def _metric_style(self, key: str, raw) -> str:
        base = f"font-family:{theme.FONT_MONO};font-weight:700;font-size:14px;"
        color = theme.TEXT
        if isinstance(raw, (int, float)) and raw == raw:  # exclude NaN
            if key in self._SIGNED:
                color = theme.UP if raw > 0 else theme.DOWN if raw < 0 else theme.TEXT
            elif key == "avg_loss":
                color = theme.DOWN if raw < 0 else theme.TEXT
        return f"color:{color};{base}"

    def _hero_color(self, key: str, raw) -> str:
        good = theme.TEXT
        if isinstance(raw, (int, float)) and raw == raw:
            if key in ("roi", "annualized"):
                good = theme.UP if raw > 0 else theme.DOWN if raw < 0 else theme.TEXT
            elif key == "max_drawdown":
                good = theme.DOWN if raw > 0 else theme.TEXT
            elif key == "profit_factor":
                good = theme.UP if raw >= 1 else theme.DOWN
            elif key == "win_ratio":
                good = theme.UP if raw >= 0.5 else theme.TEXT
        return (f"color:{good};font-family:{theme.FONT_MONO};font-weight:700;"
                f"font-size:23px;border:none;")

    def _set_banner(self, verdict) -> None:
        if verdict is None:
            self._banner.setVisible(False)
            return
        level = verdict.level
        color = theme.VERDICT.get(level, theme.WARN)
        reason = verdict.reasons[0] if verdict.reasons else ""
        self._banner.setText(f"⚠  OVERFIT RISK · {level.upper()}  —  {reason}")
        self._banner.setStyleSheet(
            f"padding:8px 10px;border-radius:6px;font-weight:700;"
            f"color:{color};background:rgba(0,0,0,0.25);border:1px solid {color};"
        )
        self._banner.setVisible(True)

    # --- derived $/annualized/time-in-market (computed from report + bars) ---

    @staticmethod
    def _derive(report, bars):
        eq = report.equity_curve or []
        initial = eq[0] if eq else None
        roi_dollars = (report.final_equity - initial) if initial is not None else None
        # exact $ drawdown from the equity curve (peak-to-trough)
        dd_dollars, peak = None, float("-inf")
        if eq:
            worst = 0.0
            for v in eq:
                peak = v if v > peak else peak
                worst = max(worst, peak - v)
            dd_dollars = worst
        # annualized ROI from the real bar time-span
        annualized = None
        years = None
        if bars and len(bars) > 1:
            span_ms = bars[-1].ts - bars[0].ts
            years = span_ms / _YEAR_MS if span_ms > 0 else None
            if years and years > 1e-6 and initial and initial > 0 and report.final_equity > 0:
                try:
                    annualized = (report.final_equity / initial) ** (1.0 / years) - 1.0
                except OverflowError:  # sub-day spans -> astronomically large exponent
                    annualized = float("inf")
        # time in market: fraction of the span spent holding a position
        time_in_market = None
        if bars and len(bars) > 1:
            span_ms = bars[-1].ts - bars[0].ts
            if span_ms > 0:
                held = sum(max(0, t.exit_ts - t.entry_ts) for t in report.trades)
                time_in_market = min(1.0, held / span_ms)
        wins = sum(1 for t in report.trades if t.pnl > 0)
        return {
            "roi_dollars": roi_dollars,
            "dd_dollars": dd_dollars,
            "annualized": annualized,
            "time_in_market": time_in_market,
            "wins": wins,
            "n": len(report.trades),
        }

    def _fill_trades(self, trades, mfe_mae=None) -> None:
        self._report_trades = list(trades)
        self._trades.setRowCount(len(trades))
        for r, t in enumerate(trades):
            ret = t.pnl / (abs(t.size) * t.entry_price) if t.size and t.entry_price else 0.0
            mfe, mae = mfe_mae[r] if mfe_mae and r < len(mfe_mae) else (None, None)
            cells = [
                str(r + 1),
                "LONG" if t.size >= 0 else "SHORT",
                f"{t.entry_price:,.2f}",
                f"{t.exit_price:,.2f}",
                f"{abs(t.size):g}",
                f"{t.pnl:,.2f}",
                f"{ret * 100:.2f}%",
                f"{mfe * 100:.2f}%" if mfe is not None else "—",
                f"{mae * 100:.2f}%" if mae is not None else "—",
            ]
            up = t.pnl >= 0
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 1:
                    item.setForeground(QtGui.QColor(theme.UP if t.size >= 0 else theme.DOWN))
                elif c in (5, 6):
                    item.setForeground(QtGui.QColor(theme.UP if up else theme.DOWN))
                elif c == 7:                                  # MFE = best excursion -> green
                    item.setForeground(QtGui.QColor(theme.UP))
                elif c == 8:                                  # MAE = worst excursion -> red
                    item.setForeground(QtGui.QColor(theme.DOWN))
                if c >= 2:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self._trades.setItem(r, c, item)

    def _on_trade_clicked(self, row: int, _col: int) -> None:
        """Trade-row click — selection only; price-chart focus lives in the Chart space now."""
        return

    def _on_run_clicked(self, row: int, _col: int) -> None:
        """Run-row click -> re-display that stored run (iterate-and-compare loop)."""
        if 0 <= row < len(self._runs):
            r = self._runs[row]
            self.show_report(r["report"], r["bars"], r["overlays"])

    # --- public ---

    def show_report(self, report, bars=None, overlays=None) -> None:
        """Display a TesterReport: hero KPIs + detail grid + equity curve + trades."""
        self.last_report = report
        self._status.setVisible(False)
        self._status.setText("")

        d = self._derive(report, bars)

        # hero tiles
        self._hero_val["roi"].setText(self._pct(report.total_return))
        self._hero_sub["roi"].setText(self._money(d["roi_dollars"]))
        self._hero_val["annualized"].setText(self._pct(d["annualized"]))
        self._hero_sub["annualized"].setText("")
        self._hero_val["win_ratio"].setText(self._pct(report.win_rate))
        self._hero_sub["win_ratio"].setText(f"{d['wins']} of {d['n']}")
        self._hero_val["max_drawdown"].setText(self._pct(report.max_drawdown))
        self._hero_sub["max_drawdown"].setText(
            self._money(-d["dd_dollars"]) if d["dd_dollars"] is not None else ""
        )
        self._hero_val["time_in_market"].setText(self._pct(d["time_in_market"]))
        self._hero_sub["time_in_market"].setText("")
        pf = report.profit_factor
        self._hero_val["profit_factor"].setText(self._fmt("profit_factor", pf))
        self._hero_sub["profit_factor"].setText("")
        for key, val in (
            ("roi", report.total_return), ("annualized", d["annualized"]),
            ("win_ratio", report.win_rate), ("max_drawdown", report.max_drawdown),
            ("profit_factor", pf),
        ):
            self._hero_val[key].setStyleSheet(self._hero_color(key, val))

        # detail grid
        for key, _ in self._DETAIL:
            raw = getattr(report, key, None)
            self._value_labels[key].setText(self._fmt(key, raw))
            self._value_labels[key].setStyleSheet(self._metric_style(key, raw))

        self._set_banner(report.verdict)

        if report.equity_curve:
            try:
                self._equity.set_data(report.equity_curve)
            except Exception:  # noqa: BLE001
                pass

        mm = report_extras.mfe_mae(report.trades, bars) if bars else None
        self._fill_trades(report.trades, mm)
        self._update_distribution(report_extras.trade_returns(report.trades))
        self._tabs.setCurrentIndex(0)  # land on the Equity tab — the headline view

    def add_run(self, report, bars=None, overlays=None) -> None:
        """Record a run in the history table (versioned) and display it."""
        self._runs.append({"report": report, "bars": bars, "overlays": overlays})
        n = len(self._runs)
        row = n - 1
        self._runs_table.insertRow(row)
        cells = [
            str(n),
            self._pct(report.total_return),
            self._pct(report.max_drawdown),
            str(report.n_trades),
            self._fmt("sharpe", report.sharpe),
        ]
        for c, text in enumerate(cells):
            item = QtWidgets.QTableWidgetItem(text)
            if c == 1:
                item.setForeground(QtGui.QColor(theme.UP if report.total_return >= 0 else theme.DOWN))
            elif c == 2:
                item.setForeground(QtGui.QColor(theme.DOWN if report.max_drawdown > 0 else theme.TEXT))
            if c >= 1:
                item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            self._runs_table.setItem(row, c, item)
        self.show_report(report, bars, overlays)
        self.toast(f"✓ Backtest complete · run {n}")

    def toast(self, msg: str) -> None:
        """Transient success line (green)."""
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{theme.UP};font-size:11px;padding:4px 8px;")
        self._status.setVisible(True)

    def show_error(self, msg: str) -> None:
        """Display an error message; clear any previous report."""
        self.last_report = None
        self._report_trades = []
        self._banner.setVisible(False)
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{theme.DOWN};font-size:11px;padding:4px 8px;")
        self._status.setVisible(True)
        for lbl in self._value_labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(self._metric_style("", None))
        for lbl in self._hero_val.values():
            lbl.setText("—")
        for lbl in self._hero_sub.values():
            lbl.setText("")
        self._trades.setRowCount(0)

    def clear(self) -> None:
        """Reset to blank state (including run history)."""
        self.last_report = None
        self._report_trades = []
        self._runs = []
        self._banner.setVisible(False)
        self._status.setVisible(False)
        for lbl in self._value_labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(self._metric_style("", None))
        for lbl in self._hero_val.values():
            lbl.setText("—")
        for lbl in self._hero_sub.values():
            lbl.setText("")
        self._trades.setRowCount(0)
        self._runs_table.setRowCount(0)


# ---------------------------------------------------------------------------
# ChatPanel
# ---------------------------------------------------------------------------

_EXAMPLES = [
    "SMA crossover on BTC with a 2×ATR trailing stop",
    "RSI mean-reversion — buy below 30, exit above 55",
    "Donchian breakout with volatility-scaled sizing",
]


class _ExampleCard(QtWidgets.QFrame):
    """A clickable example-prompt card (fills the prompt input when clicked)."""

    clicked = QtCore.Signal(str)

    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setStyleSheet(
            f"_ExampleCard{{background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:10px;}}"
            f"_ExampleCard:hover{{border-color:{theme.ACCENT};}}"
        )
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(13, 11, 13, 11)
        label = QtWidgets.QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{theme.TEXT2};border:none;background:transparent;")
        lay.addWidget(label)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:  # noqa: N802
        if event.button() == QtCore.Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._text)
        super().mouseReleaseEvent(event)


class ChatPanel(QtWidgets.QWidget):
    """Empty-state (heading + example cards) → chat log, plus prompt input + AI button."""

    promptSubmitted = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        # --- empty state (shown until the first message) ---
        self._empty = self._build_empty()
        root.addWidget(self._empty, stretch=1)

        # --- chat log (hidden until the first message) ---
        self._log = QtWidgets.QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"QTextEdit{{background:{theme.PANEL};border:1px solid {theme.BORDER};"
            f"border-radius:10px;padding:8px;color:{theme.TEXT2};}}"
        )
        self._log.hide()
        root.addWidget(self._log, stretch=1)

        # input row
        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(6)
        self._prompt_input = QtWidgets.QLineEdit()
        self._prompt_input.setPlaceholderText("Describe a strategy …")
        self._prompt_input.returnPressed.connect(self._submit)
        input_row.addWidget(self._prompt_input, stretch=1)

        self._btn_ask = QtWidgets.QPushButton("✦ Ask AI")
        self._btn_ask.setObjectName("play")
        self._btn_ask.clicked.connect(self._submit)
        input_row.addWidget(self._btn_ask)
        root.addLayout(input_row)

    def _build_empty(self) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(6, 16, 6, 6)
        v.setSpacing(7)

        eyebrow = QtWidgets.QLabel("✦ AI STUDIO")
        eyebrow.setStyleSheet(f"color:{theme.ACCENT};font-size:10px;font-weight:700;letter-spacing:2px;")
        heading = QtWidgets.QLabel("Let's build a strategy")
        heading.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;")
        subtitle = QtWidgets.QLabel("Describe an idea in plain English, or start from an example below.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{theme.TEXT2};font-size:12px;")
        v.addWidget(eyebrow)
        v.addWidget(heading)
        v.addWidget(subtitle)
        v.addSpacing(6)

        for ex in _EXAMPLES:
            card = _ExampleCard(ex)
            card.clicked.connect(self._prompt_from_card)
            v.addWidget(card)
        v.addStretch(1)
        return box

    def _prompt_from_card(self, text: str) -> None:
        self._prompt_input.setText(text)
        self._prompt_input.setFocus()

    def append_message(self, role: str, text: str) -> None:
        """Append a message to the log with a role prefix (revealing the log on first use)."""
        if self._empty.isVisible():
            self._empty.hide()
            self._log.show()
        color = {
            "system": theme.TEXT3,
            "user": theme.TEXT,
            "assistant": theme.BLUE,
        }.get(role, theme.TEXT2)
        self._log.append(
            f'<span style="color:{color};font-weight:700">{role.upper()}:</span> '
            f'<span style="color:{theme.TEXT2}">{html.escape(text)}</span>'
        )

    def _submit(self) -> None:
        prompt = self._prompt_input.text().strip()
        if prompt:
            self.promptSubmitted.emit(prompt)
            self._prompt_input.clear()

    def set_busy(self, busy: bool) -> None:
        """Disable the prompt + Ask button while an AI request is in flight."""
        self._btn_ask.setEnabled(not busy)
        self._prompt_input.setEnabled(not busy)


# ---------------------------------------------------------------------------
# ChatWorker
# ---------------------------------------------------------------------------

class ChatWorker(QtCore.QThread):
    """Background thread: calls develop_strategy and emits the AgentResult."""

    result = QtCore.Signal(object)

    def __init__(self, client, prompt: str, bars: list, config):
        super().__init__()
        self._client = client
        self._prompt = prompt
        self._bars = bars
        self._config = config

    def run(self) -> None:
        try:
            from vike_trader_app.ai.agent import develop_strategy
            res = develop_strategy(
                self._prompt, self._bars, client=self._client, config=self._config
            )
        except Exception as exc:  # noqa: BLE001
            from vike_trader_app.ai.agent import AgentResult
            res = AgentResult(
                code="", explanation=str(exc), accepted=False,
                attempts=0, problems=[str(exc)],
            )
        self.result.emit(res)


# ---------------------------------------------------------------------------
# SegmentedControl
# ---------------------------------------------------------------------------

class SegmentedControl(QtWidgets.QWidget):
    """A pill group of mutually-exclusive options (TradeLocker's resolution selector).

    Exclusive checkable buttons in a rounded track; emits ``valueChanged(str)``. Options
    can be disabled (e.g. resolutions finer than the loaded base data, which we can't
    synthesize). Use ``value()`` / ``setValue()`` to read/set the active option.
    """

    valueChanged = QtCore.Signal(str)

    def __init__(self, options, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"SegmentedControl{{background:{theme.PANEL2};border:1px solid {theme.BORDER};"
            f"border-radius:9px;}}"
        )
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QtWidgets.QPushButton] = {}
        for opt in options:
            btn = QtWidgets.QPushButton(opt)
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;border-radius:7px;"
                f"padding:6px 14px;color:{theme.TEXT3};font-weight:600;}}"
                f"QPushButton:hover{{color:{theme.TEXT2};}}"
                f"QPushButton:checked{{background:{theme.RAISE};color:{theme.TEXT};}}"
                f"QPushButton:disabled{{color:{theme.TEXT3};}}"
            )
            btn.clicked.connect(lambda _c, o=opt: self.valueChanged.emit(o))
            self._group.addButton(btn)
            row.addWidget(btn)
            self._buttons[opt] = btn

    def set_enabled_options(self, enabled) -> None:
        """Enable only the options in ``enabled`` (an iterable of labels); disable the rest."""
        allow = set(enabled)
        for opt, btn in self._buttons.items():
            btn.setEnabled(opt in allow)

    def value(self) -> str | None:
        for opt, btn in self._buttons.items():
            if btn.isChecked():
                return opt
        return None

    def setValue(self, opt: str) -> None:  # noqa: N802 - Qt-style setter
        btn = self._buttons.get(opt)
        if btn is not None and btn.isEnabled():
            btn.setChecked(True)


# ---------------------------------------------------------------------------
# BacktestConfigDialog
# ---------------------------------------------------------------------------

# resolution label -> window in epoch ms (parse_timeframe wants lowercase units, so we
# keep an explicit display-label map that includes the upper-case 1H/4H/1D forms).
_RESOLUTIONS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1H": 3_600_000, "4H": 14_400_000, "1D": 86_400_000,
}


def _base_resolution_ms(bars) -> int | None:
    """Infer the loaded data's base bar spacing (min positive gap across a sample)."""
    if not bars or len(bars) < 2:
        return None
    gaps = [bars[i + 1].ts - bars[i].ts for i in range(min(len(bars) - 1, 200))]
    pos = [g for g in gaps if g > 0]
    return min(pos) if pos else None


class BacktestConfigDialog(QtWidgets.QDialog):
    """Per-run config — capital + resolution + date range over the loaded bars.

    Mirrors TradeLocker's "Backtest" modal: a segmented resolution selector (resolutions
    finer than the loaded base data are disabled — we synthesize coarser bars by OHLCV
    aggregation but never invent finer ones), plus starting capital and a date sub-range.
    """

    def __init__(self, bars, capital: float = 10_000.0, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Backtest configuration")
        self.setModal(True)
        self.setMinimumWidth(440)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        eyebrow = QtWidgets.QLabel("BACKTEST")
        eyebrow.setStyleSheet(f"color:{theme.ACCENT};font-size:10px;font-weight:700;letter-spacing:2px;")
        title = QtWidgets.QLabel("Run configuration")
        title.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;")
        root.addWidget(eyebrow)
        root.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)

        # resolution segmented control (disable options finer than the base data)
        self._base_ms = _base_resolution_ms(bars)
        self.resolution = SegmentedControl(list(_RESOLUTIONS.keys()))
        if self._base_ms is not None:
            enabled = [lbl for lbl, ms in _RESOLUTIONS.items() if ms >= self._base_ms]
            self.resolution.set_enabled_options(enabled or list(_RESOLUTIONS.keys()))
            # default to the base resolution if it lines up, else the finest enabled
            base_lbl = next((lbl for lbl, ms in _RESOLUTIONS.items() if ms == self._base_ms), None)
            self.resolution.setValue(base_lbl or (enabled[0] if enabled else "1m"))
        else:
            self.resolution.setValue("1m")
        form.addRow("Resolution", self.resolution)

        self.capital = QtWidgets.QDoubleSpinBox()
        self.capital.setRange(1.0, 1e9)
        self.capital.setDecimals(2)
        self.capital.setPrefix("$ ")
        self.capital.setGroupSeparatorShown(True)
        self.capital.setValue(float(capital))
        form.addRow("Starting capital", self.capital)

        self.start = QtWidgets.QDateEdit()
        self.end = QtWidgets.QDateEdit()
        for w in (self.start, self.end):
            w.setCalendarPopup(True)
        if bars:
            d0 = QtCore.QDateTime.fromMSecsSinceEpoch(int(bars[0].ts)).date()
            d1 = QtCore.QDateTime.fromMSecsSinceEpoch(int(bars[-1].ts)).date()
            for w in (self.start, self.end):
                w.setDateRange(d0, d1)
            self.start.setDate(d0)
            self.end.setDate(d1)
        form.addRow("Start date", self.start)
        form.addRow("End date", self.end)
        root.addLayout(form)

        if bars:
            note = QtWidgets.QLabel(f"{len(bars):,} base bars loaded")
            note.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
            root.addWidget(note)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        run = btns.addButton("Run", QtWidgets.QDialogButtonBox.AcceptRole)
        run.setObjectName("play")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def values(self):
        """(capital, start_ts, end_ts, resolution_ms) — dates inclusive (epoch ms).

        ``resolution_ms`` is None when the chosen resolution equals the base data (no
        resampling needed); otherwise it's the coarse window to aggregate the bars to.
        """
        cap = self.capital.value()
        start_ts = QtCore.QDateTime(self.start.date(), QtCore.QTime(0, 0, 0)).toMSecsSinceEpoch()
        end_ts = QtCore.QDateTime(self.end.date(), QtCore.QTime(23, 59, 59)).toMSecsSinceEpoch()
        res_lbl = self.resolution.value()
        res_ms = _RESOLUTIONS.get(res_lbl)
        if res_ms is not None and self._base_ms is not None and res_ms <= self._base_ms:
            res_ms = None  # same as (or finer than) base -> no resampling
        return cap, start_ts, end_ts, res_ms


# ---------------------------------------------------------------------------
# DiffDialog
# ---------------------------------------------------------------------------

class DiffDialog(QtWidgets.QDialog):
    """Side-by-side 'current vs AI-proposed' code diff with Apply / Reject.

    Human-in-the-loop gate (TradeLocker's AI never silently edits): removed lines shade
    red on the left, added lines shade green on the right. Applying bumps the version.
    """

    def __init__(self, current: str, proposed: str, version: int,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"AI proposed change → v{version}")
        self.setModal(True)
        self.resize(940, 580)
        root = QtWidgets.QVBoxLayout(self)
        left_html, right_html = self._diff_html(current, proposed)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self._pane(f"Current · v{version - 1} (read only)", left_html), 1)
        row.addWidget(self._pane("Proposed", right_html), 1)
        root.addLayout(row, 1)

        btns = QtWidgets.QDialogButtonBox()
        apply = btns.addButton("Apply", QtWidgets.QDialogButtonBox.AcceptRole)
        apply.setObjectName("play")
        btns.addButton("Reject", QtWidgets.QDialogButtonBox.RejectRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    @staticmethod
    def _pane(title: str, body_html: str) -> QtWidgets.QWidget:
        box = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        cap = QtWidgets.QLabel(title.upper())
        cap.setStyleSheet(f"color:{theme.TEXT3};font-size:9px;letter-spacing:1px;")
        view = QtWidgets.QTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        view.setStyleSheet(
            f"QTextEdit{{background:{theme.PANEL};border:1px solid {theme.BORDER};border-radius:6px;}}"
        )
        view.setHtml(body_html)
        v.addWidget(cap)
        v.addWidget(view, 1)
        return box

    @staticmethod
    def _diff_html(current: str, proposed: str) -> tuple[str, str]:
        cur = current.splitlines() or [""]
        new = proposed.splitlines() or [""]
        sm = difflib.SequenceMatcher(None, cur, new)
        del_bg, add_bg = "rgba(248,81,73,0.18)", "rgba(63,185,80,0.18)"

        def row(s: str, bg: str | None = None) -> str:
            style = f"background:{bg};" if bg else ""
            return (f'<div style="{style}white-space:pre;font-family:monospace;'
                    f'color:{theme.TEXT2}">{html.escape(s) or "&nbsp;"}</div>')

        left, right = [], []
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                left += [row(s) for s in cur[i1:i2]]
                right += [row(s) for s in new[j1:j2]]
            elif tag == "replace":
                left += [row(s, del_bg) for s in cur[i1:i2]]
                right += [row(s, add_bg) for s in new[j1:j2]]
            elif tag == "delete":
                left += [row(s, del_bg) for s in cur[i1:i2]]
            elif tag == "insert":
                right += [row(s, add_bg) for s in new[j1:j2]]
        return "".join(left), "".join(right)


# ---------------------------------------------------------------------------
# StudioTab
# ---------------------------------------------------------------------------

class StudioTab(QtWidgets.QWidget):
    """Three-pane studio: ChatPanel | CodeEditor | ResultsPanel with a Run toolbar."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)

        self._bars: list = []
        self._config = None          # set to TesterConfig() on first use
        self._agent_client = None
        self._worker: ChatWorker | None = None  # keep a reference so GC doesn't collect it
        self._run_capital = None     # None -> use config.cash
        self._run_range = None       # None -> full bars, else (start_ts, end_ts)
        self._run_resolution = None  # None -> base bars, else coarse window ms to resample to
        self._apply_version = 0      # AI-applied-change version (for the diff-and-apply flow)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        # toolbar — TradeLocker-style: sits directly ABOVE the code editor (its pane header),
        # not at the top of the whole Studio tab. A wrapping FlowLayout so the buttons never
        # force the editor pane (and the window) wider than the screen.
        toolbar = FlowLayout(margin=0, h_spacing=6, v_spacing=6)
        self._toolbar = toolbar
        self._btn_run = QtWidgets.QPushButton("Run")
        self._btn_run.setObjectName("play")
        self._btn_run.clicked.connect(self.run_code)
        toolbar.addWidget(self._btn_run)
        self._btn_optimize = QtWidgets.QPushButton("⚖ Walk-forward")
        self._btn_optimize.setToolTip("Walk-forward optimize the PARAM_GRID + attach an overfit verdict")
        self._btn_optimize.clicked.connect(self._optimize)
        toolbar.addWidget(self._btn_optimize)
        self._btn_templates = QtWidgets.QPushButton("📁")
        self._btn_templates.setToolTip("Templates")
        self._btn_templates.clicked.connect(self._open_templates)
        toolbar.addWidget(self._btn_templates)
        self._btn_config = QtWidgets.QPushButton("⚙")
        self._btn_config.setToolTip("Settings")
        self._btn_config.clicked.connect(self._open_config)
        toolbar.addWidget(self._btn_config)
        # Indicators moved to the chart's own toolbar (the ƒx button) — not duplicated here.
        self._btn_export = QtWidgets.QPushButton("⤓ Export CSV")
        self._btn_export.clicked.connect(self._export_csv)
        toolbar.addWidget(self._btn_export)

        self.chat = ChatPanel()
        self.editor = CodeEditor()
        self.results = ResultsPanel()

        # editor pane = toolbar header + code editor (so the buttons sit above the editor)
        editor_pane = QtWidgets.QWidget()
        ep = QtWidgets.QVBoxLayout(editor_pane)
        ep.setContentsMargins(0, 0, 0, 0)
        ep.setSpacing(5)
        toolbar_row = QtWidgets.QWidget()
        toolbar_row.setLayout(toolbar)
        _sp = toolbar_row.sizePolicy()
        _sp.setHeightForWidth(True)
        _sp.setVerticalPolicy(QtWidgets.QSizePolicy.Minimum)
        toolbar_row.setSizePolicy(_sp)
        ep.addWidget(toolbar_row)
        ep.addWidget(self.editor, 1)

        # Bottom: two half-width cards — code editor (left) | AI Studio chat (right).
        self._bottom = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._bottom.addWidget(editor_pane)
        self._bottom.addWidget(self.chat)
        self._bottom.setStretchFactor(0, 1)
        self._bottom.setStretchFactor(1, 1)
        self._bottom.setSizes([1000, 1000])
        self._bottom.setCollapsible(0, False)
        self._bottom.setCollapsible(1, True)

        # Top: the tabbed results (Equity | Performance | Trades | Runs | Distribution [| Chart]).
        # mount_chart() adds the price chart as the trailing "Chart" tab. Reports/chart on top,
        # editor | AI-studio chat below — one tab strip up top, two cards below.
        self._vsplit = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._vsplit.addWidget(self.results)
        self._vsplit.addWidget(self._bottom)
        self._vsplit.setStretchFactor(0, 3)
        self._vsplit.setStretchFactor(1, 2)
        self._vsplit.setCollapsible(0, False)
        self._vsplit.setCollapsible(1, True)
        root.addWidget(self._vsplit, stretch=1)

        self.chat.promptSubmitted.connect(self._on_prompt)

    # --- hosted panels (moved in from the Chart space / right dock) ---

    def mount_controls(self, bar: QtWidgets.QWidget) -> None:
        """Merge the replay/data control bar into the Studio toolbar as one row.

        Appended after the Studio action buttons; the bar's trailing slider takes the row's
        stretch, so the Studio toolbar's own trailing stretch is dropped first.
        """
        last = self._toolbar.count() - 1
        item = self._toolbar.itemAt(last)
        if item is not None and item.spacerItem() is not None:
            self._toolbar.takeAt(last)
        self._toolbar.addWidget(bar, 1)

    def mount_bots(self, bots: QtWidgets.QWidget) -> None:
        """Host the Bots panel as the leftmost card of the bottom row (moved from a dock)."""
        self._bottom.insertWidget(0, bots)
        self._bottom.setCollapsible(0, True)

    def mount_chart(self, chart: QtWidgets.QWidget) -> None:
        """Host the price chart (with its playback controls) as the 'Chart' tab in the results,
        after Distribution — reports and the chart switch via the one top tab strip."""
        self.results.mount_chart_tab(chart)

    # --- state setters ---

    def set_bars(self, bars: list) -> None:
        """Set bar data used by Run and the AI agent."""
        self._bars = bars

    def set_config(self, config) -> None:
        """Set TesterConfig for runs."""
        self._config = config

    def set_agent_client(self, client) -> None:
        """Set AI client (ClaudeClient); pass None to disable AI."""
        self._agent_client = client

    def set_text(self, code: str) -> None:
        """Set editor text."""
        self.editor.setText(code)

    def text(self) -> str:
        """Return editor text."""
        return self.editor.text()

    # --- run ---

    def run_code(self) -> None:
        """Load the strategy from the editor and run a single backtest, recording it.

        Honors the per-run config (starting capital + date-range slice) set via the
        Settings modal; falls back to the full bars + the tab's TesterConfig otherwise.
        """
        from dataclasses import replace

        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        from vike_trader_app.tester import StrategyTester, TesterConfig

        code = self.editor.text()
        config = self._config if self._config is not None else TesterConfig()
        if self._run_capital is not None:
            config = replace(config, cash=self._run_capital)
        bars = self._effective_bars()
        try:
            cls = load_strategy_from_string(code, validate=True)
            report = StrategyTester(cls(), bars, config).run()
            overlays = {}
            try:
                overlays = cls().chart_overlays([b.close for b in bars]) or {}
            except Exception:  # noqa: BLE001 - overlays are optional, never block the run
                overlays = {}
            self.results.add_run(report, bars, overlays)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"{type(exc).__name__}: {exc}")

    def _effective_bars(self) -> list:
        """Apply the per-run date slice + resolution resample to the loaded base bars."""
        bars = self._bars
        if self._run_range is not None:
            s, e = self._run_range
            bars = [b for b in self._bars if s <= b.ts <= e] or self._bars
        if self._run_resolution is not None and bars:
            from vike_trader_app.core.timeframe import resample
            coarse = resample(bars, self._run_resolution)
            if len(coarse) >= 2:           # never resample down to an unusable handful
                bars = coarse
        return bars

    def _open_templates(self) -> None:
        """Open the strategy-template gallery; chosen template loads into the editor."""
        from .templates import StrategyTemplateDialog

        dlg = StrategyTemplateDialog(parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # don't accumulate dialogs across opens
        dlg.loadRequested.connect(self._load_template)
        dlg.exec()

    def _load_template(self, code: str) -> None:
        if self.editor.text().strip():
            ok = QtWidgets.QMessageBox.question(
                self, "Load template", "Replace the current editor contents with this template?"
            )
            if ok != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        self.editor.setText(code)

    def _optimize(self) -> None:
        """Walk-forward optimize the strategy's PARAM_GRID; show the stitched OOS run + overfit verdict.

        This is the "optimize safely" path: parameters are picked per train window and scored
        OUT-OF-SAMPLE, and the stitched result carries a PBO/deflated-Sharpe overfit verdict (the
        banner) — so a curve-fit grid sweep shows up as High overfit risk rather than a shiny number.
        """
        from dataclasses import replace

        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        from vike_trader_app.tester import StrategyTester, TesterConfig

        config = self._config if self._config is not None else TesterConfig()
        if self._run_capital is not None:
            config = replace(config, cash=self._run_capital)
        bars = self._effective_bars()
        try:
            cls = load_strategy_from_string(self.editor.text(), validate=True)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"{type(exc).__name__}: {exc}")
            return
        grid = getattr(cls, "PARAM_GRID", {}) or {}
        if not grid:
            self.results.toast("Add a PARAM_GRID to the strategy to walk-forward optimize it.")
            return
        if len(bars) < 120:
            self.results.toast("Need ≥120 bars to walk-forward optimize.")
            return
        self.results.toast("Optimizing + walk-forward validating…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            wf = StrategyTester(cls(), bars, config).walk_forward(cls.make, grid, n_splits=3)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"Optimize failed: {type(exc).__name__}: {exc}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.results.add_run(wf.oos_report, bars)
        best = wf.windows[-1].best_params if wf.windows else {}
        level = wf.oos_report.verdict.level if wf.oos_report.verdict else "?"
        self.results.toast(f"Walk-forward OOS · overfit risk: {level} · best {best}")

    def _export_csv(self) -> None:
        """Export the current run's metrics + trades to a CSV file."""
        report = self.results.last_report
        if report is None:
            self.results.toast("Run a backtest first — nothing to export.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export report CSV", "report.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(report_extras.report_to_csv(report))
            self.results.toast(f"Exported → {path}")
        except OSError as exc:
            self.results.show_error(f"Export failed: {exc}")

    def _open_indicators(self) -> None:
        """Open the indicator catalogue; chosen snippet is appended to the editor."""
        from .indicators import IndicatorCatalogDialog

        dlg = IndicatorCatalogDialog(parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.insertRequested.connect(self._insert_snippet)
        dlg.exec()

    def _insert_snippet(self, snippet: str) -> None:
        current = self.editor.text()
        sep = "" if not current or current.endswith("\n\n") else ("\n" if current.endswith("\n") else "\n\n")
        self.editor.setText(current + sep + snippet)

    def _open_config(self) -> None:
        """Open the per-run backtest-config modal (capital + date range)."""
        from vike_trader_app.tester import TesterConfig

        if not self._bars:
            QtWidgets.QMessageBox.information(self, "Backtest settings", "Load data first.")
            return
        cap = self._run_capital
        if cap is None:
            cap = (self._config.cash if self._config is not None else TesterConfig().cash)
        dlg = BacktestConfigDialog(self._bars, capital=cap, parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)  # freed after exec returns (deferred delete)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            cap, start_ts, end_ts, res_ms = dlg.values()
            self._run_capital = cap
            self._run_range = (start_ts, end_ts)
            self._run_resolution = res_ms
            res_lbl = dlg.resolution.value() or "base"
            self.results.toast(
                f"Settings · capital ${cap:,.0f} · {res_lbl} · range set — press Run"
            )

    # --- chat ---

    def _on_prompt(self, prompt: str) -> None:
        if self._agent_client is None:
            self.chat.append_message("system", "No AI client configured.")
            return
        if self._worker is not None and self._worker.isRunning():
            self.chat.append_message("system", "AI is still working — please wait…")
            return

        from vike_trader_app.tester import TesterConfig
        config = self._config if self._config is not None else TesterConfig()
        worker = ChatWorker(self._agent_client, prompt, self._bars, config)
        self._worker = worker
        worker.result.connect(self._on_agent_result)
        worker.finished.connect(self._on_worker_finished)
        self.chat.set_busy(True)
        self.chat.append_message("user", prompt)
        worker.start()

    def _on_agent_result(self, res) -> None:
        if res.code:
            current = self.editor.text()
            if current.strip():
                # human-in-the-loop: review the AI's change as a diff before applying
                self._apply_version += 1
                dlg = DiffDialog(current, res.code, self._apply_version, parent=self)
                dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
                if dlg.exec() == QtWidgets.QDialog.Accepted:
                    self.editor.setText(res.code)
                    self.chat.append_message("system", f"Applied AI change — v{self._apply_version}.")
                else:
                    self._apply_version -= 1
                    self.chat.append_message("system", "AI change rejected.")
            else:
                self.editor.setText(res.code)  # empty editor -> just load it
        if res.explanation:
            self.chat.append_message("assistant", res.explanation)
        elif not res.accepted and res.problems:
            self.chat.append_message("system", "Agent failed: " + "; ".join(res.problems))

    def _on_worker_finished(self) -> None:
        """Release the finished worker (only now is the QThread truly done) + re-enable input."""
        self.chat.set_busy(False)
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()

    def shutdown(self) -> None:
        """Wait for any in-flight AI worker so we never destroy a running QThread on close."""
        worker = self._worker
        if worker is not None and worker.isRunning():
            worker.wait(3000)
