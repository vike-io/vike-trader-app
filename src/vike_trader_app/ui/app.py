"""The vike-trader-app desktop app: a visual backtester in the vike.io look.

Dockable layout (QDockWidget): Markets + Strategy on the left, the candle/equity
charts and replay bar in the centre, Backtest Report + Trades on the right, with a
full-width header. The "⚠ Validate" button runs the anti-overfit report and lights
up the verdict banner — the differentiator.
"""

import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis import metrics
from ..core.engine import BacktestEngine
from ..core.paper import PaperTester, pump
from ..core.strategy_loader import load_strategy_from_file
from ..data.binance_source import interval_ms
from ..data.cache import get_bars, is_stale
from ..data.polling_feed import PollingBarFeed
from ..data.sources import select_source
from ..data.store import RunRecord, Store
from . import icons, theme
from .chart import EquityChart, PriceChart
from .dialogs import LoadDataDialog, default_strategy_factory
from .panels import (
    TradesTable,
    WatchlistPanel,
    strategy_params,
)
from .replay import Replay
from .studio import StudioTab
from .alerts import AlertsTab
from .journal import JournalTab
from .screener import ScreenerTab
from .tools import ToolsTab

_SPEEDS = [1, 2, 5, 10, 25, 50]  # bars advanced per timer tick
_DAY_MS = 86_400_000
_WATCHLIST_DAYS = 7  # history pulled when clicking a watchlist symbol
_WATCHLIST_FRESH_MS = 5 * 60_000  # cache-first: reuse cached bars if the last one is this fresh
_DB_PATH = "storage/db/vike_trader_app.sqlite"
_FORWARD_SEED_BARS = 250  # warm-up history pulled before a forward run starts
_FORWARD_FEE = 0.001
_FORWARD_CASH = 10_000.0


class _LiveFeedWorker(QtCore.QThread):
    """Runs a LiveBarFeed's async loop off the UI thread; marshals bars back via a signal."""

    barReceived = QtCore.Signal(object)  # Bar
    failed = QtCore.Signal(str)

    def __init__(self, feed):
        super().__init__()
        self._feed = feed
        self._stop = False

    def run(self):
        import asyncio

        try:
            asyncio.run(self._feed.run_forever(self.barReceived.emit, stop=lambda: self._stop))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
            self.failed.emit(str(exc))

    def stop(self):
        self._stop = True


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("vike-trader-app   Backtester")  # space name updated on tab change
        self.setWindowIcon(icons.brand_icon(theme.ACCENT, theme.BG))  # brand V in the title bar
        self.resize(1440, 900)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QtWidgets.QMainWindow.AnimatedDocks | QtWidgets.QMainWindow.AllowNestedDocks
        )

        self._bars = []
        self._result = None
        self._replay = Replay(0)
        self._strategy_factory = default_strategy_factory()
        self._symbol = "BTCUSDT"
        self._interval = "1m"

        # forward (paper) mode state
        self._forward = None      # PaperTester while live, else None
        self._feed = None         # PollingBarFeed (poll fallback)
        self._fwd_worker = None   # _LiveFeedWorker (push, preferred)
        self._fwd_bars = []       # live bars received this run (charted)
        self._refresh_timer = None  # round-robin live quote refresh (started after cache fill)
        self._refresh_q = []        # crypto symbols cycled by the quote refresh

        # widgets
        self.price = PriceChart()
        self.equity = EquityChart()
        self.trades = TradesTable()
        self.watchlist = WatchlistPanel()
        from .bots_panel import BotsPanel
        self.bots = BotsPanel()
        self.strategy = self.bots.strategy   # alias: existing code calls self.strategy.show_strategy
        self.history = self.bots.history     # alias: existing code calls self.history.update_runs
        self.store = Store(str(Path(_DB_PATH)))
        self.watchlist.symbolChosen.connect(self._load_symbol)
        self.bots.runChosen.connect(self._open_run)
        self.bots.launchRequested.connect(self._launch_bot)

        self.setMenuWidget(self._build_header())
        self._build_central()
        self._build_docks()
        self.setStatusBar(self._build_statusbar())
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._wire_panels_toggle()

        self.strategy.show_strategy(self._strategy_factory)
        self.history.update_runs(self.store.list_runs())
        self._populate_watchlist()

        self._fwd_timer = QtCore.QTimer(self)
        self._fwd_timer.timeout.connect(self._forward_poll_tick)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._on_tick)
        self._clock = QtCore.QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)
        self._tick_clock()

    # --- header ---
    def _build_header(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setStyleSheet(f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};")
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(12, 7, 12, 7)
        row.setSpacing(14)

        # Brand + active space both live in the OS title bar now ("vike-trader-app — Studio"),
        # and the V is the window/taskbar icon — so the header carries only the data crumb.
        # (The clock is in the status bar, bottom-right.)
        self._mode_tag = QtWidgets.QLabel("CHART")
        self._mode_tag.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;"
        )
        self.crumb = QtWidgets.QLabel("No data loaded")
        self.crumb.setStyleSheet(f"color:{theme.TEXT2};")

        row.addWidget(self._mode_tag)
        row.addWidget(self.crumb)
        row.addStretch(1)
        return bar

    # --- central charts + replay ---
    def _build_central(self):
        # Chart space shows price candles only — equity moved to Studio's results.
        charts = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        charts.addWidget(self.price)
        charts.setStretchFactor(0, 1)

        container = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(container)
        outer.setContentsMargins(7, 7, 7, 7)
        outer.setSpacing(7)
        outer.addWidget(charts, 1)
        outer.addWidget(self._build_controls())

        # The Backtester (charts + replay) and the Studio (AI strategy dev) are sibling
        # tabs of one window — the Studio reuses the same charts/tester under the hood.
        self._backtester = container
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(container, "Chart")
        self.studio = StudioTab()
        self._wire_studio_agent()
        self.tabs.addTab(self.studio, "Studio")
        self.tools = ToolsTab()
        self.tabs.addTab(self.tools, "Tools")
        self.screener = ScreenerTab()
        self.tabs.addTab(self.screener, "Screener")
        self.journal = JournalTab()
        self.tabs.addTab(self.journal, "Journal")
        self.alerts = AlertsTab()
        self.tabs.addTab(self.alerts, "Alerts")

        # The left icon rail is the PRIMARY navigation (TradeLocker-style) — the horizontal
        # tab strip is hidden, so the rail alone switches between the six spaces.
        self.tabs.tabBar().hide()
        self.setCentralWidget(self.tabs)
        # The rail lives in the LEFT TOOLBAR AREA so it sits flush against the window's left
        # edge — *outside* (left of) the Markets/Strategy dock area, like a VS Code / TradeLocker
        # activity bar. Inside the central widget it rendered to the right of the left docks,
        # which put Markets at x=0 and the rail at x=146 — the bug we're fixing.
        rail_tb = QtWidgets.QToolBar("Navigation")
        rail_tb.setObjectName("railbar")
        rail_tb.setMovable(False)
        rail_tb.setFloatable(False)
        rail_tb.setContentsMargins(0, 0, 0, 0)
        rail_tb.addWidget(self._build_icon_rail())
        self.addToolBar(QtCore.Qt.LeftToolBarArea, rail_tb)

    _RAIL_ITEMS = [
        ("▤", "Chart"), ("✦", "Studio"), ("⚙", "Tools"),
        ("⊞", "Screener"), ("☰", "Journal"), ("◉", "Alerts"),
    ]

    # PANELS section of the rail: independent show/hide toggles (TradeLocker style).
    # (key, icon_name, tooltip, shortcut). "backtester" toggles the centre chart; the others
    # map to docks in _panel_dock_map.
    _PANELS = [
        ("backtester", "chart", "Chart", "Ctrl+G"),
        ("market", "market", "Market watch", "Ctrl+M"),
        ("strategies", "strategies", "Strategies", "Ctrl+B"),
        ("trades", "trades", "Trades & Positions", "Ctrl+T"),
    ]

    def _rail_section(self, text: str) -> QtWidgets.QLabel:
        """A tiny SPACES/PANELS section caption for the icon rail (TradeLocker-style)."""
        lbl = QtWidgets.QLabel(text)
        lbl.setAlignment(QtCore.Qt.AlignHCenter)
        lbl.setStyleSheet(
            f"color:{theme.TEXT3};font-size:8px;font-weight:700;letter-spacing:1px;border:none;"
        )
        return lbl

    def _chip_tip(self, name: str, shortcut: str | None = None) -> str:
        """Rich hover tooltip: the panel/space name + a shortcut 'chip' (TradeLocker-style)."""
        if not shortcut:
            return name
        return (
            f"<span style='color:{theme.TEXT};'>{name}</span> &nbsp;"
            f"<span style='background:{theme.RAISE};color:{theme.TEXT2};"
            f"font-family:{theme.FONT_MONO};'>&nbsp;{shortcut}&nbsp;</span>"
        )

    def _build_icon_rail(self) -> QtWidgets.QWidget:
        rail = QtWidgets.QWidget()
        rail.setFixedWidth(54)
        rail.setStyleSheet(f"background:{theme.PANEL};border-right:1px solid {theme.BORDER};")
        col = QtWidgets.QVBoxLayout(rail)
        col.setContentsMargins(7, 10, 7, 10)
        col.setSpacing(6)

        # No brand mark here — the V now lives in the OS title bar (+ taskbar icon), so the rail
        # opens straight at the SPACES section instead of duplicating the logo.
        col.addWidget(self._rail_section("SPACES"), 0, QtCore.Qt.AlignHCenter)
        col.addSpacing(2)

        self._rail_group = QtWidgets.QButtonGroup(self)
        self._rail_group.setExclusive(True)
        btn_qss = (
            f"QToolButton{{background:transparent;border:none;border-radius:11px;"
            f"color:{theme.TEXT3};font-size:18px;}}"
            f"QToolButton:hover{{background:{theme.RAISE};color:{theme.TEXT2};}}"
            f"QToolButton:checked{{background:{theme.RAISE};color:{theme.ACCENT};}}"
        )
        for i, (glyph, name) in enumerate(self._RAIL_ITEMS):
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(name.lower(), theme.TEXT3, theme.ACCENT, theme.TEXT2))
            b.setIconSize(QtCore.QSize(22, 22))
            b.setToolTip(self._chip_tip(name))
            b.setCheckable(True)
            b.setFixedSize(40, 40)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            b.clicked.connect(lambda _c, idx=i: self.tabs.setCurrentIndex(idx))
            self._rail_group.addButton(b, i)
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        col.addStretch(1)

        # PANELS section — independent show/hide toggles for the three docks (TradeLocker-style).
        # Wired to the docks in _wire_panels_toggle() once _build_docks() has created them.
        col.addWidget(self._rail_section("PANELS"), 0, QtCore.Qt.AlignHCenter)
        col.addSpacing(2)
        self._panel_btns: dict[str, QtWidgets.QToolButton] = {}
        for key, icon_name, tip, sc in self._PANELS:
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(icon_name, theme.TEXT3, theme.ACCENT, theme.TEXT2))
            b.setIconSize(QtCore.QSize(22, 22))
            b.setToolTip(self._chip_tip(tip, sc))
            b.setCheckable(True)
            b.setChecked(True)
            b.setFixedSize(40, 40)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            self._panel_btns[key] = b
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        col.addSpacing(6)

        demo = QtWidgets.QLabel("DEMO")
        demo.setAlignment(QtCore.Qt.AlignCenter)
        demo.setStyleSheet(
            f"color:{theme.ACCENT};font-size:9px;font-weight:700;letter-spacing:1px;"
            f"border:1px solid rgba(255,106,0,0.4);border-radius:6px;padding:3px 0;"
        )
        col.addWidget(demo, 0, QtCore.Qt.AlignHCenter)

        first = self._rail_group.button(0)
        if first is not None:
            first.setChecked(True)
        return rail

    def _build_statusbar(self) -> QtWidgets.QStatusBar:
        sb = QtWidgets.QStatusBar()
        sb.setSizeGripEnabled(False)
        sb.setStyleSheet(
            f"QStatusBar{{background:{theme.PANEL};border-top:1px solid {theme.BORDER};}}"
            f"QStatusBar::item{{border:none;}}"
        )
        self.foot_status = QtWidgets.QLabel("Ready")
        self.foot_status.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;padding:0 6px;")
        sb.addWidget(self.foot_status)

        self.foot_info = QtWidgets.QLabel("No data loaded")
        self.foot_info.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;padding:0 6px;")
        sb.addPermanentWidget(self.foot_info)
        self._feed_badge = QtWidgets.QLabel("● BINANCE")
        self._feed_badge.setStyleSheet(
            f"color:{theme.UP};font-size:10px;background:{theme.BG};"
            f"border:1px solid {theme.BORDER};border-radius:20px;padding:3px 10px;margin-right:6px;"
        )
        sb.addPermanentWidget(self._feed_badge)
        # clock lives at the bottom-right now (moved out of the header)
        self.clock = QtWidgets.QLabel("--:--:--")
        self.clock.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;padding:0 6px;")
        sb.addPermanentWidget(self.clock)
        return sb

    def _feed_label(self, symbol: str) -> str:
        """Provider for the feed badge — Binance for crypto, Yahoo/Dukascopy for forex.

        Binance has no forex, so a forex symbol must not claim a Binance feed.
        """
        from ..data.sources import is_forex_symbol

        return "YAHOO · DUKASCOPY" if is_forex_symbol(symbol) else "BINANCE"

    def _update_feed_badge(self, *, live: bool = False) -> None:
        prefix = "● LIVE · " if live else "● "
        self._feed_badge.setText(f"{prefix}{self._feed_label(self._symbol)}")

    def _build_controls(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setStyleSheet(
            f"background:{theme.PANEL};border:1px solid {theme.BORDER};border-radius:8px;"
        )
        row = QtWidgets.QHBoxLayout(bar)
        row.setContentsMargins(9, 7, 9, 7)
        row.setSpacing(8)

        self.btn_load = QtWidgets.QPushButton("⤓ Load data")
        self.btn_strategy = QtWidgets.QPushButton("⟐ Load strategy")
        self.btn_validate = QtWidgets.QPushButton("⚠ Validate")
        self.btn_validate.setObjectName("validate")
        self.btn_optimize = QtWidgets.QPushButton("⚙ Optimize")
        self.btn_back = QtWidgets.QPushButton("◀")
        self.btn_play = QtWidgets.QPushButton("▶ Play")
        self.btn_play.setObjectName("play")
        self.btn_fwd = QtWidgets.QPushButton("▶|")
        self.btn_full = QtWidgets.QPushButton("⤒ End")
        self.btn_forward = QtWidgets.QPushButton("● Forward (paper)")
        self.btn_forward.setObjectName("forward")
        self.btn_forward.setToolTip("Paper-trade the current strategy on live bars (no real orders)")
        self.btn_load.clicked.connect(self._open_load_dialog)
        self.btn_strategy.clicked.connect(self._load_strategy)
        self.btn_validate.clicked.connect(self._validate)
        self.btn_optimize.clicked.connect(self._optimize)
        self.btn_back.clicked.connect(self._step_back)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_fwd.clicked.connect(self._step_fwd)
        self.btn_full.clicked.connect(self._jump_end)
        self.btn_forward.clicked.connect(self._toggle_forward)

        self.speed = QtWidgets.QComboBox()
        for s in _SPEEDS:
            self.speed.addItem(f"{s}×", s)
        self.speed.setCurrentIndex(3)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.valueChanged.connect(self._on_slider)
        self.pos_label = QtWidgets.QLabel("bar 0 / 0")
        self.pos_label.setStyleSheet(f"color:{theme.TEXT2};")

        widgets = [
            self.btn_load,
            self.btn_strategy,
            self.btn_validate,
            self.btn_optimize,
            self._sep(),
            self.btn_back,
            self.btn_play,
            self.btn_fwd,
            self.btn_full,
            self._sep(),
            self.btn_forward,
            self._sep(),
        ]
        for w in widgets:
            row.addWidget(w)
        row.addWidget(self.speed)
        row.addWidget(self.slider, 1)
        row.addWidget(self.pos_label)
        return bar

    def _sep(self):
        line = QtWidgets.QFrame()
        line.setFixedWidth(1)
        line.setStyleSheet(f"background:{theme.BORDER};")
        return line

    # --- docks ---
    def _dock(self, title, widget):
        d = QtWidgets.QDockWidget(title.upper(), self)
        d.setFeatures(
            QtWidgets.QDockWidget.DockWidgetMovable | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        d.setWidget(widget)
        return d

    def _build_docks(self):
        # TradeLocker-style information architecture:
        #   Strategies (left) · Chart (centre) · Market watch + Report (right) ·
        #   Trades & Positions (full-width bottom).
        # The bottom area owns both lower corners so the trades strip spans the full width,
        # with the side docks sitting above it.
        self.setCorner(QtCore.Qt.BottomLeftCorner, QtCore.Qt.BottomDockWidgetArea)
        self.setCorner(QtCore.Qt.BottomRightCorner, QtCore.Qt.BottomDockWidgetArea)

        market = self._dock("Market watch", self.watchlist)
        strategies = self._dock("Bots", self.bots)
        trades = self._dock("Trades & Positions", self._build_trades_panel())

        # RIGHT: Market watch on top, Strategies beneath it — both toggle from the rail.
        # No "Backtest Report" panel: TradeLocker's trader screen has none, and backtest
        # analysis (metrics/verdict) belongs to the Studio tab's ResultsPanel, not here.
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, market)
        self.splitDockWidget(market, strategies, QtCore.Qt.Vertical)
        # BOTTOM: Trades & Positions, spanning the full width.
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, trades)

        # rail PANELS toggle targets (key must match _PANELS)
        self._market_dock = market
        self._panel_dock_map = {"market": market, "strategies": strategies, "trades": trades}
        self.resizeDocks([market], [300], QtCore.Qt.Horizontal)
        self.resizeDocks([trades], [190], QtCore.Qt.Vertical)
        self._docks = [market, strategies, trades]

    def _scroll(self, widget):
        sc = QtWidgets.QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QtWidgets.QFrame.NoFrame)
        sc.setWidget(widget)
        return sc

    def _build_trades_panel(self) -> QtWidgets.QWidget:
        """Trades & Positions: an account summary strip (balance/equity/PnL) over the trades."""
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        summary = QtWidgets.QWidget()
        summary.setObjectName("acctbar")
        summary.setStyleSheet(
            f"#acctbar{{background:{theme.PANEL2};border-bottom:1px solid {theme.BORDER};}}"
        )
        row = QtWidgets.QHBoxLayout(summary)
        row.setContentsMargins(12, 6, 12, 6)
        row.setSpacing(26)
        self._acct: dict[str, QtWidgets.QLabel] = {}
        for key, label in [("balance", "Balance"), ("equity", "Equity"),
                           ("pnl", "P&L"), ("ret", "Return")]:
            cell = QtWidgets.QVBoxLayout()
            cell.setSpacing(1)
            cap = QtWidgets.QLabel(label.upper())
            cap.setStyleSheet(f"color:{theme.TEXT3};font-size:9px;letter-spacing:.5px;border:none;")
            val = QtWidgets.QLabel("—")
            val.setStyleSheet(
                f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-weight:700;border:none;"
            )
            self._acct[key] = val
            cell.addWidget(cap)
            cell.addWidget(val)
            row.addLayout(cell)
        row.addStretch(1)

        v.addWidget(summary)
        v.addWidget(self.trades, 1)
        return w

    def _update_account(self) -> None:
        """Refresh the Trades & Positions summary strip from the current result."""
        if not hasattr(self, "_acct"):
            return
        res = self._result
        if res is None or not res.equity_curve:
            for v in self._acct.values():
                v.setText("—")
            return
        eq = res.equity_curve
        initial, final = eq[0], res.final_equity
        pnl = final - initial
        ret = metrics.total_return(eq) * 100
        self._acct["balance"].setText(f"${initial:,.2f}")
        self._acct["equity"].setText(f"${final:,.2f}")
        sign = "+" if pnl >= 0 else "−"
        color = theme.UP if pnl >= 0 else theme.DOWN
        self._acct["pnl"].setText(f"{sign}${abs(pnl):,.2f}")
        self._acct["pnl"].setStyleSheet(
            f"color:{color};font-family:{theme.FONT_MONO};font-weight:700;border:none;"
        )
        self._acct["ret"].setText(f"{ret:+.2f}%")
        self._acct["ret"].setStyleSheet(
            f"color:{color};font-family:{theme.FONT_MONO};font-weight:700;border:none;"
        )

    def _wire_panels_toggle(self) -> None:
        """Wire each rail PANELS toggle (+ its Ctrl shortcut) to its dock — independently.

        Docks have no close button, so each toggle button is the single source of truth for
        "user wants this panel shown"; we never sync button<-dock (which would fight the
        tab-driven hide on Studio/Tools). Intent per panel is remembered so switching back to
        the Backtester restores whatever the user last chose.
        """
        self._panel_visible: dict[str, bool] = {}
        for key, _glyph, _tip, shortcut in self._PANELS:
            self._panel_visible[key] = True
            self._panel_btns[key].toggled.connect(
                lambda on, k=key: self._toggle_panel(k, on)
            )
            sc = QtGui.QShortcut(QtGui.QKeySequence(shortcut), self)
            sc.activated.connect(self._panel_btns[key].toggle)

    def _toggle_panel(self, key: str, on: bool) -> None:
        self._panel_visible[key] = on
        if self.tabs.currentWidget() is not self._backtester:
            return
        if key == "backtester":
            # Hide the centre (chart + controls); QMainWindow expands the docks to fill.
            self.tabs.setVisible(on)
        else:
            self._panel_dock_map[key].setVisible(on)

    def _on_tab_changed(self, index: int) -> None:
        """Show the Backtester docks only on the Backtester tab (Studio/Tools are full-width)."""
        on_backtester = self.tabs.currentWidget() is self._backtester
        # The centre must be visible to show any non-Backtester space; on the Backtester space
        # itself, honor the "backtester" hide toggle.
        self.tabs.setVisible(
            self._panel_visible.get("backtester", True) if on_backtester else True
        )
        for d in self._docks:
            d.setVisible(on_backtester)
        if on_backtester:  # honor each panel's manual hide when returning to the Backtester
            for key, dock in getattr(self, "_panel_dock_map", {}).items():
                dock.setVisible(self._panel_visible.get(key, True))
        btn = self._rail_group.button(index)  # keep the icon rail in sync with the tabs
        if btn is not None:
            btn.setChecked(True)
        # the OS title bar is the active-space indicator now (tab strip + header chip are gone)
        if 0 <= index < len(self._RAIL_ITEMS):
            self.setWindowTitle(f"vike-trader-app   {self._RAIL_ITEMS[index][1]}")

    def _wire_studio_agent(self) -> None:
        """Give the Studio a live Claude client iff an API key + the [ai] extra are present.

        No key -> the Studio's AI chat stays in the graceful 'No AI client configured' mode
        (and we avoid importing anthropic on every launch).
        """
        import os

        if not os.environ.get("ANTHROPIC_API_KEY"):
            return
        try:
            from ..ai.llm import ClaudeClient

            self.studio.set_agent_client(ClaudeClient())
        except Exception:  # noqa: BLE001 - missing [ai] extra / bad key -> stay in no-AI mode
            pass

    # --- data / strategy loading ---
    def load_bars(self, bars, strategy_factory=None, *, record=True):
        if strategy_factory is not None:
            self._strategy_factory = strategy_factory
        self.strategy.show_strategy(self._strategy_factory)
        self._bars = bars
        self.studio.set_bars(bars)  # the Studio tab backtests the same data
        self._result = BacktestEngine(bars, self._strategy_factory()).run()
        self._replay = Replay(len(bars))
        self.price.set_data(bars, self._result.trades)
        self.price.set_overlays(self._strategy_factory().chart_overlays([b.close for b in bars]))
        self.equity.set_data(self._result.equity_curve)
        self.trades.update_trades(self._result.trades)
        self.slider.setMaximum(self._replay.last_index)
        self.slider.setValue(self._replay.last_index)
        if bars:
            last = bars[-1].close
            self.crumb.setText(
                f"{self._symbol}  ·  {self._interval}  ·  {last:,.2f}  ·  {len(bars):,} bars"
            )
            self.foot_info.setText(f"{self._symbol} · {self._interval} · {len(bars):,} bars")
            self.foot_status.setText("Loaded")
        self._update_feed_badge()
        self._update_account()
        self._render_frame()
        if record and bars:
            self._save_run()

    def _launch_bot(self) -> None:
        """Launch Bot: run the active strategy on the loaded bars and record it.

        If no data is loaded yet, open the load dialog first. A successful run drops
        markers on the chart and appears under Historic Runs (via _save_run)."""
        if not self._bars:
            self._open_load_dialog()
            return
        self.load_bars(self._bars, record=True)

    def _save_run(self):
        """Persist the just-finished backtest to the history store."""
        if not self._bars or self._result is None:
            return
        eq = self._result.equity_curve
        rec = RunRecord(
            ts=int(time.time() * 1000),
            symbol=self._symbol,
            interval=self._interval,
            strategy=self._strategy_factory.__name__,
            start_ts=self._bars[0].ts,
            end_ts=self._bars[-1].ts,
            n_bars=len(self._bars),
            net_return=metrics.total_return(eq),
            final_equity=self._result.final_equity,
            trades=len(self._result.trades),
            win_rate=metrics.win_rate(self._result.trades),
            profit_factor=metrics.profit_factor(self._result.trades),
            max_drawdown=metrics.max_drawdown(eq),
            sharpe=metrics.sharpe(eq),
            params=strategy_params(self._strategy_factory),
        )
        self.store.save_run(rec)
        self.history.update_runs(self.store.list_runs())

    def _open_run(self, rec):
        """Reopen a past run: reload its exact data window from cache and re-run."""
        self.crumb.setText(f"Reopening {rec.symbol} {rec.interval}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            bars = get_bars(rec.symbol, rec.interval, rec.start_ts, rec.end_ts,
                            fetcher=select_source(rec.symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - report load failure
            QtWidgets.QMessageBox.warning(self, "Reopen failed", f"{rec.symbol}: {exc}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._symbol = rec.symbol
        self._interval = rec.interval
        self.load_bars(bars, record=False)

    def _open_load_dialog(self):
        dlg = LoadDataDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.bars:
            self._symbol = dlg.symbol.text().strip() or self._symbol
            self._interval = dlg.interval.currentText()
            self.load_bars(dlg.bars)

    def _populate_watchlist(self):
        """Fill the watchlist from the local cache so every row maps to loadable data.

        Symbols are grouped Crypto / Forex / Other; last close + change fill in via a
        background reader. Falls back to the panel's built-in demo list if nothing is cached.
        """
        from ..data.catalog import Catalog

        cat = Catalog()
        symbols = cat.symbols()
        if not symbols:
            return
        crypto, forex, other = [], [], []
        for s in symbols:
            if s.endswith(("USDT", "USDC", "BUSD")):
                crypto.append(s)
            elif len(s) == 6 and s.isalpha():
                forex.append(s)
            else:
                other.append(s)
        groups = [("Crypto", crypto), ("Forex", forex), ("Other", other)]
        self.watchlist.set_symbols([(g, syms) for g, syms in groups if syms])
        self._crypto_syms = crypto       # round-robin set for the live quote refresh
        self._start_price_fill(symbols)  # fill last price / forex bid-ask progressively

    def _start_price_fill(self, symbols) -> None:
        """Fill watchlist quotes on the MAIN thread, a few symbols per timer tick.

        The Parquet/Catalog reader isn't safe for concurrent reads, so a background thread
        would race with the user's data loads and crash (``data/`` is owned by the parallel
        instance — read-only here). Reading on the main thread keeps it serialized with loads;
        a QTimer chunks the work so first paint and clicks stay responsive while prices tick in.
        """
        from ..data.catalog import Catalog

        self._price_cat = Catalog()
        self._price_queue = list(symbols)
        self._price_timer = QtCore.QTimer(self)
        self._price_timer.timeout.connect(self._fill_price_chunk)
        self._price_timer.start(10)

    @staticmethod
    def _quote_from_bars(bars):
        """``(last_close, 24h_change_frac)`` for a watchlist quote, or None if no bars."""
        if not bars:
            return None
        last = bars[-1]
        cutoff = last.ts - _DAY_MS  # ~24h change reference
        ref = next((b for b in bars if b.ts >= cutoff), bars[0])
        chg = (last.close / ref.close - 1.0) if ref.close else 0.0
        return (last.close, chg)

    def _push_watch_quote(self, symbol, bars) -> None:
        """Update one watchlist row's quote from freshly loaded bars (keeps it in sync)."""
        quote = self._quote_from_bars(bars)
        if quote is not None:
            self.watchlist.set_prices({symbol: quote})

    def _fill_price_chunk(self) -> None:
        now = int(time.time() * 1000)
        start = now - 3 * _DAY_MS  # a few days back to clear forex weekend gaps
        chunk: dict[str, float] = {}
        for _ in range(2):
            if not self._price_queue:
                self._price_timer.stop()
                self._start_quote_refresh()  # cache painted; now keep quotes live
                break
            sym = self._price_queue.pop(0)
            try:
                quote = self._quote_from_bars(self._price_cat.query(sym, "1m", start, now))
                if quote is not None:
                    chunk[sym] = quote
            except Exception:  # noqa: BLE001 - a missing/locked file just yields no price
                continue
        if chunk:
            self.watchlist.set_prices(chunk)

    def _start_quote_refresh(self) -> None:
        """Keep crypto Market Watch quotes live by topping up one symbol per tick from Binance.

        Crypto only: Binance trades 24/7 and responds quickly, so a gentle round-robin keeps
        prices current without a startup network storm (forex weekend gaps would just spin).
        Runs on the main thread — the Parquet layer isn't safe for background reads — and
        relies on ``get_bars`` being incremental: a fresh symbol is a cheap cache read, a stale
        one fetches only the last few bars.
        """
        self._refresh_q = list(getattr(self, "_crypto_syms", []))
        if not self._refresh_q:
            return
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_quote_tick)
        self._refresh_timer.start(6000)  # one symbol per 6s -> a full lap every ~Nx6s

    def _refresh_quote_tick(self) -> None:
        """Top up the next crypto symbol's recent bars and refresh its watchlist quote."""
        if self._forward is not None or not self._refresh_q:
            return  # forward mode owns the network; idle if nothing to refresh
        sym = self._refresh_q.pop(0)
        self._refresh_q.append(sym)  # rotate to the back
        now = int(time.time() * 1000)
        start = now - _WATCHLIST_DAYS * _DAY_MS
        try:
            cached = self._price_cat.query(sym, "1m", start, now)
            if cached and not is_stale(cached[-1].ts, now, _WATCHLIST_FRESH_MS):
                self._push_watch_quote(sym, cached)  # already fresh -> no network
                return
            bars = get_bars(sym, "1m", start, now, fetcher=select_source(sym).fetch_bars_range)
            self._push_watch_quote(sym, bars)
        except Exception:  # noqa: BLE001 - a transient fetch failure just skips this tick
            return

    def _load_symbol(self, symbol):
        """Load a symbol, topping up the recent gap so the newest bars are always shown.

        Cache-first: a *fresh* cached tail (newest bar within ``_WATCHLIST_FRESH_MS``) paints
        instantly with zero network. Otherwise we fetch only the missing recent gap before
        painting — ``get_bars`` is incremental, so it pulls just the bars after the last cached
        one, never a full re-download. (The old logic served deep-but-hours-stale history
        without ever fetching the gap, which is why the chart lagged behind Binance.) If the
        top-up fetch fails we fall back to whatever is cached rather than leaving an empty chart.
        """
        now = int(time.time() * 1000)
        start = now - _WATCHLIST_DAYS * _DAY_MS

        from ..data.catalog import Catalog
        cached = Catalog().query(symbol, "1m", start, now)
        if cached and not is_stale(cached[-1].ts, now, _WATCHLIST_FRESH_MS):
            self._symbol, self._interval = symbol, "1m"     # fresh -> paint instantly
            self.load_bars(cached)
            self._push_watch_quote(symbol, cached)
            return

        self.crumb.setText(f"Loading {symbol}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            bars = get_bars(symbol, "1m", start, now, progress=self._fetch_progress,
                            fetcher=select_source(symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - network/load failure
            if cached:  # offline / fetch failed -> show cached rather than nothing
                self._symbol, self._interval = symbol, "1m"
                self.load_bars(cached)
                self._push_watch_quote(symbol, cached)
                self.crumb.setText(f"{symbol}: latest unavailable, showing cached · {exc}")
                return
            QtWidgets.QMessageBox.warning(self, "Load failed", f"{symbol}: {exc}")
            self.crumb.setText("No data loaded")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._symbol = symbol
        self._interval = "1m"
        self.load_bars(bars)
        self._push_watch_quote(symbol, bars)

    def _fetch_progress(self, done, start, end):
        pct = (done - start) / max(end - start, 1) * 100
        self.crumb.setText(f"Loading {self._symbol}…  {pct:.0f}%")
        QtWidgets.QApplication.processEvents()

    def _load_strategy(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load strategy (.py)", "", "Python (*.py)"
        )
        if not path:
            return
        try:
            self._strategy_factory = load_strategy_from_file(path)
        except Exception as exc:  # noqa: BLE001 - report load failure to the user
            QtWidgets.QMessageBox.critical(self, "Load failed", str(exc))
            return
        self.strategy.show_strategy(self._strategy_factory)
        if self._bars:
            self.load_bars(self._bars)

    def _optimize(self):
        if not self._bars:
            QtWidgets.QMessageBox.information(self, "Optimize", "Load data first.")
            return
        from .optimizer import show_optimizer

        show_optimizer(self, self._bars, self._strategy_factory, fee_rate=0.001)

    def _validate(self):
        if not self._bars:
            QtWidgets.QMessageBox.information(self, "Validate", "Load data first.")
            return
        grid = getattr(self._strategy_factory, "PARAM_GRID", {})
        if not grid:
            QtWidgets.QMessageBox.information(
                self,
                "Validate",
                f"{self._strategy_factory.__name__} declares no PARAM_GRID, so there is "
                "nothing to optimize. Add a PARAM_GRID to enable anti-overfit checks.",
            )
            return
        self.btn_validate.setEnabled(False)
        self.crumb.setText("Validating (optimizing + anti-overfit)…")
        QtWidgets.QApplication.processEvents()
        try:
            from ..analysis.report import build_overfit_report

            report = build_overfit_report(
                self._bars, self._strategy_factory.make, grid, n_splits=4, fee_rate=0.001
            )
            v = report.verdict
            QtWidgets.QMessageBox.information(
                self,
                "Overfit check",
                f"Overfit risk: {v.level.upper()}\n\n"
                f"PBO {report.pbo:.0%}  ·  Deflated Sharpe {report.deflated_sharpe:.0%}  "
                f"·  {report.n_trials} configs\n\n"
                + "\n".join(f"• {r}" for r in v.reasons),
            )
        finally:
            self.btn_validate.setEnabled(True)
            if self._bars:
                last = self._bars[-1].close
                self.crumb.setText(
                    f"{self._symbol}  ·  1m  ·  {last:,.2f}  ·  {len(self._bars)} bars"
                )

    # --- replay wiring ---
    def _render_frame(self):
        i = self._replay.index
        self.price.show_upto(i)
        self.equity.show_upto(i)
        self.pos_label.setText(f"bar {i} / {self._replay.last_index}")
        if self.slider.value() != i:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)

    def _on_tick(self):
        for _ in range(self.speed.currentData()):
            self._replay.tick()
        if not self._replay.playing:
            self._timer.stop()
            self.btn_play.setText("▶ Play")
        self._render_frame()

    def _toggle_play(self):
        if self._replay.playing:
            self._replay.pause()
            self._timer.stop()
            self.btn_play.setText("▶ Play")
        else:
            if self._replay.at_end:
                self._replay.seek(0)
            self._replay.play()
            self._timer.start()
            self.btn_play.setText("⏸ Pause")

    def _step_fwd(self):
        self._replay.step()
        self._render_frame()

    def _step_back(self):
        self._replay.step_back()
        self._render_frame()

    def _jump_end(self):
        self._replay.seek(self._replay.last_index)
        self._render_frame()

    def _on_slider(self, value):
        self._replay.seek(value)
        self._render_frame()

    # --- forward (paper) mode ---
    def _toggle_forward(self):
        if self._forward is not None:
            self._stop_forward()
        else:
            self._start_forward()

    def _start_forward(self):
        """Seed warm-up history, then stream live closed bars into a PaperTester."""
        symbol, interval = self._symbol, self._interval
        src = select_source(symbol)  # crypto -> vike/binance; forex -> Yahoo+Dukascopy
        self.crumb.setText(f"Forward: seeding {symbol} {interval}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            now = int(time.time() * 1000)
            start = now - _FORWARD_SEED_BARS * interval_ms(interval)
            seed = src.fetch_bars_range(symbol, interval, start, now)
        except Exception as exc:  # noqa: BLE001 - network/seed failure
            QtWidgets.QMessageBox.warning(self, "Forward failed", f"Could not seed {symbol}: {exc}")
            self.crumb.setText("No data loaded")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self._forward = PaperTester(
            symbol=symbol, interval=interval, strategy=self._strategy_factory(),
            cash=_FORWARD_CASH, fee_rate=_FORWARD_FEE, seed_bars=seed,
            store=self.store, on_step=None, created_ts=int(time.time() * 1000),
        )
        self._fwd_bars = []
        self._set_backtest_controls_enabled(False)
        self.btn_forward.setText("■ Stop forward")
        self._update_feed_badge(live=True)

        # Prefer the push WebSocket feed (lower latency) when the source has one;
        # forex has no push feed, so it falls straight through to REST polling.
        if not (src.supports_live_ws and self._start_live_worker(symbol, interval)):
            self._feed = PollingBarFeed(
                symbol, interval, fetch_latest=src.make_fetch_latest(symbol, interval)
            )
            self._fwd_timer.start(int(self._feed.poll_seconds * 1000))
        self.crumb.setText(f"● FORWARD (paper) · {symbol} · {interval} · waiting for bars…")

    def _start_live_worker(self, symbol, interval) -> bool:
        """Start a LiveBarFeed in a QThread. Returns False if [live]/websockets is unavailable."""
        try:
            from ..data.vike_live import make_live_feed

            import websockets  # noqa: F401 - probe the optional [live] dep before threading
        except Exception:  # noqa: BLE001 - websockets not installed -> caller polls instead
            return False
        worker = self._fwd_worker = _LiveFeedWorker(make_live_feed(symbol, interval))
        worker.barReceived.connect(self._on_forward_bar)
        worker.failed.connect(self._on_forward_failed)
        worker.start()
        return True

    def _forward_poll_tick(self):
        """REST-polling fallback: drain newly-closed bars into the tester, then repaint."""
        if self._forward is None or self._feed is None:
            return
        if pump(self._feed, self._forward):
            self._render_forward()

    def _on_forward_bar(self, bar):
        """Slot for a bar pushed from the LiveBarFeed worker thread."""
        if self._forward is None:
            return
        self._forward.on_bar_live(bar)
        self._render_forward()

    def _on_forward_failed(self, message):
        QtWidgets.QMessageBox.warning(self, "Forward feed error", message)
        self._stop_forward()

    def _render_forward(self):
        """Repaint charts/panels from the live tester state (live bars only)."""
        if self._forward is None:
            return
        self._fwd_bars = list(self._forward.engine.bars[-len(self._forward.equity_curve):]) \
            if self._forward.equity_curve else []
        res = self._forward.result()
        self.price.set_data(self._fwd_bars, res.trades)
        self.price.set_overlays(
            self._strategy_factory().chart_overlays([b.close for b in self._fwd_bars])
        )
        self.equity.set_data(res.equity_curve)
        self.price.show_upto(len(self._fwd_bars) - 1)
        self.equity.show_upto(len(res.equity_curve) - 1)
        self.trades.update_trades(res.trades)
        if self._fwd_bars:
            last = self._fwd_bars[-1].close
            self.crumb.setText(
                f"● FORWARD (paper) · {self._symbol} · {self._interval} · "
                f"{last:,.2f} · {len(self._fwd_bars)} live bars · eq {res.final_equity:,.0f}"
            )

    def _stop_forward(self):
        self._fwd_timer.stop()
        if self._fwd_worker is not None:
            self._fwd_worker.stop()
            self._fwd_worker.wait(2000)
            self._fwd_worker = None
        if self._forward is not None:
            self._forward.stop()
        self._forward = None
        self._feed = None
        self.btn_forward.setText("● Forward (paper)")
        self._update_feed_badge(live=False)
        self._set_backtest_controls_enabled(True)

    def _set_backtest_controls_enabled(self, on: bool):
        """Lock backtest/replay controls while forward mode owns the charts (and vice-versa)."""
        for w in (
            self.btn_load, self.btn_strategy, self.btn_validate, self.btn_optimize,
            self.btn_back, self.btn_play, self.btn_fwd, self.btn_full, self.slider, self.speed,
        ):
            w.setEnabled(on)

    def closeEvent(self, event):  # noqa: N802 - Qt override
        self._stop_forward()  # never leave a feed thread running
        self.studio.shutdown()  # wait out any in-flight AI worker (no destroyed-while-running)
        if getattr(self, "_price_timer", None) is not None:
            self._price_timer.stop()  # halt the watchlist price-fill ticks
        if getattr(self, "_refresh_timer", None) is not None:
            self._refresh_timer.stop()  # halt the live quote refresh
        super().closeEvent(event)

    def _tick_clock(self):
        self.clock.setText(QtCore.QTime.currentTime().toString("HH:mm:ss"))


def main():
    import sys

    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    # Windows shows python.exe's icon in the title bar/taskbar unless the process claims its own
    # AppUserModelID — then the OS uses *our* window icon (the brand V) instead.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("vike.trader.app")
        except Exception:  # noqa: BLE001 - non-fatal; the window icon is still set below
            pass
    app.setWindowIcon(icons.brand_icon(theme.ACCENT, theme.BG))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
