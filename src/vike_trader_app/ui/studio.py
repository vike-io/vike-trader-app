"""Studio tab: ChatPanel | CodeEditor | ResultsPanel with Run wiring and ChatWorker thread."""

from PySide6 import QtCore, QtWidgets

from . import theme
from .editor import CodeEditor


# ---------------------------------------------------------------------------
# ResultsPanel
# ---------------------------------------------------------------------------

class ResultsPanel(QtWidgets.QWidget):
    """Verdict banner + metrics grid + status label for a tester run."""

    _METRIC_DEFS = [
        ("n_trades",        "Trades"),
        ("total_return",    "Total return"),
        ("net_profit",      "Net profit"),
        ("sharpe",          "Sharpe"),
        ("max_drawdown",    "Max drawdown"),
        ("profit_factor",   "Profit factor"),
        ("win_rate",        "Win rate"),
        ("expected_payoff", "Expected payoff"),
        ("recovery_factor", "Recovery factor"),
    ]

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # verdict banner
        self._banner = QtWidgets.QLabel()
        self._banner.setVisible(False)
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            f"padding:8px;border-radius:6px;font-weight:700;background:{theme.PANEL2};"
            f"border:1px solid {theme.BORDER};"
        )
        root.addWidget(self._banner)

        # metrics grid
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        self._value_labels: dict[str, QtWidgets.QLabel] = {}
        for i, (key, label) in enumerate(self._METRIC_DEFS):
            lbl = QtWidgets.QLabel(label + ":")
            lbl.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;")
            val = QtWidgets.QLabel("—")
            val.setStyleSheet(f"color:{theme.TEXT};font-weight:600;")
            self._value_labels[key] = val
            grid.addWidget(lbl, i, 0)
            grid.addWidget(val, i, 1)
        root.addLayout(grid)

        # status label (error / info)
        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        root.addWidget(self._status)
        root.addStretch(1)

        self.last_report: object = None

    # --- public ---

    def show_report(self, report) -> None:
        """Populate metrics from a TesterReport; show verdict banner if present."""
        self.last_report = report
        self._status.setVisible(False)
        self._status.setText("")

        def _pct(v: float) -> str:
            return f"{v * 100:.2f}%"

        def _f(v: float) -> str:
            return "∞" if v == float("inf") else f"{v:.4f}"

        formatters = {
            "total_return": _pct,
            "max_drawdown": _pct,
            "win_rate":     _pct,
        }

        for key, _ in self._METRIC_DEFS:
            raw = getattr(report, key, None)
            if raw is None:
                text = "—"
            elif key in formatters:
                text = formatters[key](raw)
            elif isinstance(raw, int):
                text = str(raw)
            else:
                text = _f(float(raw))
            self._value_labels[key].setText(text)

        # verdict banner
        if report.verdict is not None:
            level = report.verdict.level
            color = theme.VERDICT.get(level, theme.WARN)
            reason = report.verdict.reasons[0] if report.verdict.reasons else ""
            self._banner.setText(f"OVERFIT RISK · {level.upper()}  —  {reason}")
            self._banner.setStyleSheet(
                f"padding:8px;border-radius:6px;font-weight:700;"
                f"color:{color};background:rgba(0,0,0,0.25);border:1px solid {color};"
            )
            self._banner.setVisible(True)
        else:
            self._banner.setVisible(False)

    def show_error(self, msg: str) -> None:
        """Display an error message; clear any previous report."""
        self.last_report = None
        self._banner.setVisible(False)
        self._status.setText(msg)
        self._status.setStyleSheet(f"color:{theme.DOWN};font-size:11px;")
        self._status.setVisible(True)
        for lbl in self._value_labels.values():
            lbl.setText("—")

    def clear(self) -> None:
        """Reset to blank state."""
        self.last_report = None
        self._banner.setVisible(False)
        self._status.setVisible(False)
        for lbl in self._value_labels.values():
            lbl.setText("—")


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
            self.results.show_report(report)
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
