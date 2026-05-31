"""Studio tab: ChatPanel | CodeEditor | ResultsPanel with Run wiring and ChatWorker thread."""

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .chart import EquityChart, PriceChart
from .editor import CodeEditor


# ---------------------------------------------------------------------------
# ResultsPanel
# ---------------------------------------------------------------------------

class ResultsPanel(QtWidgets.QWidget):
    """Tabbed results — Chart | Performance | Trades — mirroring TradingView/TradeLocker.

    The price chart (candles + entry/exit markers) over an equity curve is the
    default view; the metric grid and the trade list are tabs. The overfit-verdict
    banner sits ABOVE the tabs so the honesty signal is always visible.
    """

    _METRIC_DEFS = [
        ("n_trades",        "Trades"),
        ("total_return",    "Total return"),
        ("net_profit",      "Net profit"),
        ("sharpe",          "Sharpe"),
        ("sortino",         "Sortino"),
        ("max_drawdown",    "Max drawdown"),
        ("profit_factor",   "Profit factor"),
        ("win_rate",        "Win rate"),
        ("expected_payoff", "Expected payoff"),
        ("recovery_factor", "Recovery factor"),
        ("avg_win",         "Avg win"),
        ("avg_loss",        "Avg loss"),
    ]
    # metrics where >0 is good (green) / <0 is bad (red)
    _SIGNED = {"total_return", "net_profit", "expected_payoff", "avg_win"}
    _PCT = {"total_return", "max_drawdown", "win_rate"}

    _TRADE_COLS = ["#", "Side", "Entry", "Exit", "Size", "PnL", "Return"]

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        # verdict banner (above the tabs — always visible)
        self._banner = QtWidgets.QLabel()
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        self._banner.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._banner)

        # status label (errors / info)
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        self._status.setContentsMargins(8, 0, 8, 0)
        root.addWidget(self._status)

        self._tabs = QtWidgets.QTabWidget()
        root.addWidget(self._tabs, 1)

        # --- Chart tab: candles + trade markers over an equity curve ---
        self._price = PriceChart()
        self._equity = EquityChart()
        chart_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        chart_split.addWidget(self._price)
        chart_split.addWidget(self._equity)
        chart_split.setStretchFactor(0, 3)
        chart_split.setStretchFactor(1, 1)
        self._tabs.addTab(chart_split, "Chart")

        # --- Performance tab: metric cards in a 2-col grid ---
        perf = QtWidgets.QWidget()
        pgrid = QtWidgets.QGridLayout(perf)
        pgrid.setContentsMargins(12, 12, 12, 12)
        pgrid.setHorizontalSpacing(16)
        pgrid.setVerticalSpacing(10)
        self._value_labels: dict[str, QtWidgets.QLabel] = {}
        ncols = 2
        for i, (key, label) in enumerate(self._METRIC_DEFS):
            r, c = divmod(i, ncols)
            cell = QtWidgets.QWidget()
            cv = QtWidgets.QVBoxLayout(cell)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(1)
            cap = QtWidgets.QLabel(label.upper())
            cap.setStyleSheet(f"color:{theme.TEXT3};font-size:9px;letter-spacing:1px;")
            val = QtWidgets.QLabel("—")
            val.setStyleSheet(self._metric_style("", None))
            cv.addWidget(cap)
            cv.addWidget(val)
            self._value_labels[key] = val
            pgrid.addWidget(cell, r, c)
        pgrid.setRowStretch((len(self._METRIC_DEFS) + ncols - 1) // ncols, 1)
        self._tabs.addTab(perf, "Performance")

        # --- Trades tab ---
        self._trades = QtWidgets.QTableWidget(0, len(self._TRADE_COLS))
        self._trades.setHorizontalHeaderLabels(self._TRADE_COLS)
        self._trades.verticalHeader().setVisible(False)
        self._trades.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._trades.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._trades.setAlternatingRowColors(True)
        _hdr = self._trades.horizontalHeader()
        _hdr.setSectionResizeMode(QtWidgets.QHeaderView.Stretch)  # share width, no h-scroll
        _hdr.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)  # "#" snug
        self._trades.cellClicked.connect(self._on_trade_clicked)
        self._tabs.addTab(self._trades, "Trades")

        self.last_report: object = None
        self._report_trades: list = []  # row index -> Trade, for the chart-focus linkage

    # --- formatting helpers ---

    def _fmt(self, key: str, raw) -> str:
        if raw is None:
            return "—"
        if isinstance(raw, int) and key not in self._PCT:
            return str(raw)
        if raw == float("inf"):
            return "∞"
        if raw == float("-inf"):
            return "−∞"
        if key in self._PCT:
            return f"{raw * 100:.2f}%"
        return f"{raw:,.2f}"

    def _metric_style(self, key: str, raw) -> str:
        base = "font-weight:700;font-size:15px;"
        color = theme.TEXT
        if isinstance(raw, (int, float)) and raw == raw:  # exclude NaN
            if key in self._SIGNED:
                color = theme.UP if raw > 0 else theme.DOWN if raw < 0 else theme.TEXT
            elif key == "max_drawdown":
                color = theme.DOWN if raw > 0 else theme.TEXT
            elif key == "profit_factor":
                color = theme.UP if raw >= 1 else theme.DOWN
            elif key == "win_rate":
                color = theme.UP if raw >= 0.5 else theme.TEXT
        return f"color:{color};{base}"

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

    def _fill_trades(self, trades) -> None:
        self._report_trades = list(trades)
        self._trades.setRowCount(len(trades))
        for r, t in enumerate(trades):
            ret = t.pnl / (abs(t.size) * t.entry_price) if t.size and t.entry_price else 0.0
            cells = [
                str(r + 1),
                "LONG" if t.size >= 0 else "SHORT",
                f"{t.entry_price:,.2f}",
                f"{t.exit_price:,.2f}",
                f"{abs(t.size):g}",
                f"{t.pnl:,.2f}",
                f"{ret * 100:.2f}%",
            ]
            up = t.pnl >= 0
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 1:
                    item.setForeground(QtGui.QColor(theme.UP if t.size >= 0 else theme.DOWN))
                elif c in (5, 6):
                    item.setForeground(QtGui.QColor(theme.UP if up else theme.DOWN))
                if c >= 2:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self._trades.setItem(r, c, item)

    def _on_trade_clicked(self, row: int, _col: int) -> None:
        """Trade-row click -> jump to the Chart tab and zoom to that trade (TradingView UX)."""
        if 0 <= row < len(self._report_trades):
            self._tabs.setCurrentIndex(0)  # Chart
            self._price.focus_ts(self._report_trades[row].entry_ts)

    # --- public ---

    def show_report(self, report, bars=None, overlays=None) -> None:
        """Populate metrics + chart + trade list from a TesterReport.

        ``bars`` (the price series) is needed to draw candles + trade markers; pass it
        from the Run path. ``overlays`` is ``{label: series}`` for indicator lines.
        """
        self.last_report = report
        self._status.setVisible(False)
        self._status.setText("")

        for key, _ in self._METRIC_DEFS:
            raw = getattr(report, key, None)
            self._value_labels[key].setText(self._fmt(key, raw))
            self._value_labels[key].setStyleSheet(self._metric_style(key, raw))

        self._set_banner(report.verdict)

        if bars:
            try:
                self._price.set_data(bars, report.trades)
                self._price.set_overlays(overlays or {})
            except Exception:  # noqa: BLE001 - charting must never break the run
                pass
        if report.equity_curve:
            try:
                self._equity.set_data(report.equity_curve)
            except Exception:  # noqa: BLE001
                pass

        self._fill_trades(report.trades)
        self._tabs.setCurrentIndex(0)  # land on the chart — the headline view

    def show_error(self, msg: str) -> None:
        """Display an error message; clear any previous report."""
        self.last_report = None
        self._report_trades = []
        self._banner.setVisible(False)
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{theme.DOWN};font-size:11px;padding:6px 8px;")
        self._status.setVisible(True)
        for lbl in self._value_labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(self._metric_style("", None))
        self._trades.setRowCount(0)

    def clear(self) -> None:
        """Reset to blank state."""
        self.last_report = None
        self._report_trades = []
        self._banner.setVisible(False)
        self._status.setVisible(False)
        for lbl in self._value_labels.values():
            lbl.setText("—")
            lbl.setStyleSheet(self._metric_style("", None))
        self._trades.setRowCount(0)


# ---------------------------------------------------------------------------
# ChatPanel
# ---------------------------------------------------------------------------

_CHIPS = ["SMA crossover on BTC", "RSI mean-reversion", "breakout with ATR stop"]


class ChatPanel(QtWidgets.QWidget):
    """Chat log + prompt input + AI button + example chips."""

    promptSubmitted = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        self._log = QtWidgets.QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            f"QTextEdit{{background:{theme.PANEL};border:1px solid {theme.BORDER};"
            f"border-radius:6px;padding:4px;color:{theme.TEXT2};}}"
        )
        root.addWidget(self._log, stretch=1)

        # example chips
        chips_row = QtWidgets.QHBoxLayout()
        chips_row.setSpacing(4)
        for chip_text in _CHIPS:
            btn = QtWidgets.QPushButton(chip_text)
            btn.setStyleSheet(
                f"QPushButton{{background:{theme.PANEL2};color:{theme.TEXT2};"
                f"border:1px solid {theme.BORDER};border-radius:4px;padding:3px 8px;font-size:10px;}}"
                f"QPushButton:hover{{color:{theme.TEXT};border-color:{theme.BORDER2};}}"
            )
            btn.clicked.connect(lambda _checked, t=chip_text: self._prompt_input.setText(t))
            chips_row.addWidget(btn)
        chips_row.addStretch(1)
        root.addLayout(chips_row)

        # input row
        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(4)
        self._prompt_input = QtWidgets.QLineEdit()
        self._prompt_input.setPlaceholderText("Describe a strategy …")
        self._prompt_input.returnPressed.connect(self._submit)
        input_row.addWidget(self._prompt_input, stretch=1)

        self._btn_ask = QtWidgets.QPushButton("Ask AI")
        self._btn_ask.clicked.connect(self._submit)
        input_row.addWidget(self._btn_ask)
        root.addLayout(input_row)

    def append_message(self, role: str, text: str) -> None:
        """Append a message to the log with a role prefix."""
        color = {
            "system": theme.TEXT3,
            "user": theme.TEXT,
            "assistant": theme.BLUE,
        }.get(role, theme.TEXT2)
        self._log.append(
            f'<span style="color:{color};font-weight:700">{role.upper()}:</span> '
            f'<span style="color:{theme.TEXT2}">{text}</span>'
        )

    def _submit(self) -> None:
        prompt = self._prompt_input.text().strip()
        if prompt:
            self.promptSubmitted.emit(prompt)
            self._prompt_input.clear()


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

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        # toolbar
        toolbar = QtWidgets.QHBoxLayout()
        self._btn_run = QtWidgets.QPushButton("Run")
        self._btn_run.setObjectName("play")
        self._btn_run.clicked.connect(self.run_code)
        toolbar.addWidget(self._btn_run)
        toolbar.addStretch(1)
        root.addLayout(toolbar)

        # splitter
        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self.chat = ChatPanel()
        self.editor = CodeEditor()
        self.results = ResultsPanel()

        self._splitter.addWidget(self.chat)
        self._splitter.addWidget(self.editor)
        self._splitter.addWidget(self.results)
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)
        self._splitter.setStretchFactor(2, 3)
        self._splitter.setCollapsible(0, True)
        self._splitter.setCollapsible(1, False)
        self._splitter.setCollapsible(2, False)

        root.addWidget(self._splitter, stretch=1)

        self.chat.promptSubmitted.connect(self._on_prompt)

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
        """Load the strategy from the editor and run a single backtest."""
        from vike_trader_app.core.strategy_loader import load_strategy_from_string
        from vike_trader_app.tester import StrategyTester, TesterConfig

        code = self.editor.text()
        config = self._config if self._config is not None else TesterConfig()
        try:
            cls = load_strategy_from_string(code, validate=True)
            report = StrategyTester(cls(), self._bars, config).run()
            overlays = {}
            try:
                overlays = cls().chart_overlays([b.close for b in self._bars]) or {}
            except Exception:  # noqa: BLE001 - overlays are optional, never block the run
                overlays = {}
            self.results.show_report(report, self._bars, overlays)
        except Exception as exc:  # noqa: BLE001
            self.results.show_error(f"{type(exc).__name__}: {exc}")

    # --- chat ---

    def _on_prompt(self, prompt: str) -> None:
        if self._agent_client is None:
            self.chat.append_message("system", "No AI client configured.")
            return

        from vike_trader_app.tester import TesterConfig
        config = self._config if self._config is not None else TesterConfig()
        self._worker = ChatWorker(self._agent_client, prompt, self._bars, config)
        self._worker.result.connect(self._on_agent_result)
        self.chat.append_message("user", prompt)
        self._worker.start()

    def _on_agent_result(self, res) -> None:
        if res.code:
            self.editor.setText(res.code)
        if res.explanation:
            self.chat.append_message("assistant", res.explanation)
        elif not res.accepted and res.problems:
            self.chat.append_message("system", "Agent failed: " + "; ".join(res.problems))
