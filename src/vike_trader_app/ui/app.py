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
from ..data.cache import DEFAULT_ROOT, get_bars
from ..data.live_update import (
    closed_bars,
    feed_health,
    fetch_in_flight,
    live_fetch_window,
    merge_live_bars,
)
from ..data.polling_feed import PollingBarFeed
from ..data.rollup import load_pins, refresh_pinned
from ..data.sources import select_source
from ..data.store import RunRecord, Store
from . import icons, theme
from .bots_panel import BotsPanel
from .chart import PriceChart
from .datamanager import DataManagerTab
from .dialogs import LoadDataDialog, default_strategy_factory
from .panels import (
    TradesTable,
    WatchlistPanel,
    strategy_params,
)
from .replay import Replay
from .watchlist_data import is_stale, quote_from_bars
from .studio import StudioTab
from .alerts import AlertsTab
from .journal import JournalTab
from .news import NewsTab
from .screener import ScreenerTab
# Tools tab (standalone calculators) hidden per user request — restore by uncommenting
# this import, its addTab below, and the ("⚙", "Tools") entry in _RAIL_ITEMS.
# from .tools import ToolsTab
from .economic_calendar import EconomicCalendarTab
from .equity_calendar import CalendarSpace
from .options_tab import OptionsTab
from ..data.options.service import OptionsService

_SPEEDS = [1, 2, 5, 10, 25, 50]  # bars advanced per timer tick
_DAY_MS = 86_400_000
_WATCHLIST_DAYS = 7  # history pulled when clicking a watchlist symbol
# Days of history to pull per timeframe so the range selector (1D..5Y) has enough bars.
_INTERVAL_LOOKBACK = {"1m": 7, "3m": 7, "5m": 10, "15m": 21, "30m": 30,
                      "1h": 90, "2h": 120, "4h": 240, "1d": 1825, "1w": 3650}
_WATCHLIST_FRESH_MS = 5 * 60_000  # cache-first: reuse cached bars if the last one is this fresh
_LIVE_LOOKBACK = 5  # bars (incl. the forming candle) pulled per live chart tick
_LIVE_FETCH_TIMEOUT_MS = 60_000  # a live fetch running longer than this is presumed stuck (self-heal)
_FEED_STATES = {  # connection-watchdog badge: (colour, prefix) per state
    "live": (theme.UP, "● LIVE · "),
    "stale": (theme.WARN, "● STALE · "),
    "down": (theme.DOWN, "● RECONNECTING · "),
    "idle": (theme.TEXT3, "● "),
}
_DB_PATH = "storage/db/vike_trader_app.sqlite"
_PINS_PATH = "storage/pins.json"  # pinned (symbol, interval) series kept precomputed (rollups)
_ROLLUP_REFRESH_MS = 60_000       # backstop: keep pinned rollups current (incremental, cheap)
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


class _LiveFetchWorker(QtCore.QThread):
    """One-shot off-thread fetch of the latest bars for the live chart updater.

    Keeps the ~100-200ms REST call off the UI thread so the chart never micro-stutters on a
    poll. Safe off-thread because it only hits the network — it does NOT read Parquet/Catalog
    (the thread-unsafe path). Results marshal back to the main thread via signals.
    """

    fetched = QtCore.Signal(object)  # list[Bar]
    failed = QtCore.Signal(str)

    def __init__(self, fetch, symbol, interval, start, end):
        super().__init__()
        self._fetch, self._symbol, self._interval = fetch, symbol, interval
        self._start, self._end = start, end

    def run(self):
        try:
            self.fetched.emit(self._fetch(self._symbol, self._interval, self._start, self._end))
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread (no modal)
            self.failed.emit(str(exc))


class _RuleOverlay(QtWidgets.QWidget):
    """Transparent, click-through overlay that paints the two separator hairlines (under the
    title bar + above the status bar) in **device-pixel space**.

    A plain 1px-tall coloured widget renders differently depending on where its top lands:
    on an integer device pixel it's crisp/full, on a fractional one (common at 125%/150%
    display scaling) it smears to ~half-coverage and looks thinner+dimmer — which is why the
    two separators didn't match. Here both lines are filled as exactly one physical pixel at an
    integer device row, so they're guaranteed identical at any scaling.
    """

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self._bottom_y = 0  # logical y of the bottom rule; 0 = not placed yet
        self._vline_x = 0   # logical x of the rail separator; 0 = not placed yet

    def set_lines(self, bottom_y: int, vline_x: int) -> None:
        if (bottom_y, vline_x) != (self._bottom_y, self._vline_x):
            self._bottom_y, self._vline_x = bottom_y, vline_x
            self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QtGui.QPainter(self)
        dpr = self.devicePixelRatioF()
        painter.scale(1.0 / dpr, 1.0 / dpr)  # now drawing in physical pixels
        w = int(round(self.width() * dpr))
        h = int(round(self.height() * dpr))
        color = QtGui.QColor(theme.BORDER)
        by = int(round(self._bottom_y * dpr)) if self._bottom_y > 0 else h
        painter.fillRect(QtCore.QRect(0, 0, w, 1), color)        # under the title bar
        if self._bottom_y > 0:
            painter.fillRect(QtCore.QRect(0, by, w, 1), color)   # above the status bar
        if self._vline_x > 0:                                    # rail separator (vertical)
            painter.fillRect(QtCore.QRect(int(round(self._vline_x * dpr)), 0, 1, by), color)
        painter.end()


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"vike-trader-app   {self._RAIL_ITEMS[0][1]}")  # space name updated on tab change
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

        # live chart auto-updater + connection watchdog (main-thread polling, look-ahead-safe)
        self._live_fail_streak = 0
        self._live_base_ms = 10_000
        self._live_worker = None        # in-flight _LiveFetchWorker (kept ref'd so it isn't GC'd)
        self._live_worker_started = 0   # when it started (ms) -> abandon if it runs too long
        self._live_fetch_for = None     # (symbol, interval) the in-flight fetch is for

        # widgets
        self.price = PriceChart()         # Chart space — clean standalone viewer
        self.studio_price = PriceChart()  # Studio workspace chart — same data, driven in lockstep
        self.trades = TradesTable()
        self.watchlist = WatchlistPanel()
        self.bots = BotsPanel()
        self.strategy = self.bots.strategy   # alias: existing code calls self.strategy.show_strategy
        self.history = self.bots.history     # alias: existing code calls self.history.update_runs
        self.store = Store(str(Path(_DB_PATH)))
        self.watchlist.symbolChosen.connect(self._load_symbol)
        self.bots.runChosen.connect(self._open_run)
        self.bots.launchRequested.connect(self._launch_bot)
        # timeframe dropdown on either chart -> reload the current symbol at that interval
        self.price.intervalChosen.connect(self._on_interval_chosen)
        self.studio_price.intervalChosen.connect(self._on_interval_chosen)
        # pairs indicators need a 2nd symbol the app fetches (the chart can't reach the data layer)
        self.price.pairsRequested.connect(lambda n: self._add_pairs(self.price, n))
        self.studio_price.pairsRequested.connect(lambda n: self._add_pairs(self.studio_price, n))

        # Header crumb removed — it duplicated the chart's OHLC legend + the status bar.
        # Keep the labels as hidden status sinks so existing setText() calls (and tests) work.
        self._mode_tag = QtWidgets.QLabel("CHART", self)
        self._mode_tag.hide()
        self.crumb = QtWidgets.QLabel("No data loaded", self)
        self.crumb.hide()
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

        self._live_timer = QtCore.QTimer(self)  # auto-updates the chart for the live symbol
        self._live_timer.timeout.connect(self._live_tick)

        self._rollup_timer = QtCore.QTimer(self)  # keep pinned rollups precomputed (main thread)
        self._rollup_timer.timeout.connect(self._refresh_pinned_tick)
        self._rollup_timer.start(_ROLLUP_REFRESH_MS)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._on_tick)
        self._clock = QtCore.QTimer(self)
        self._clock.timeout.connect(self._tick_clock)
        self._clock.start(1000)
        self._tick_clock()

        # Place the window fully on-screen. We have no saved geometry, so without
        # this Windows can position a 1440-wide window past the right edge — which
        # pushed the Market watch dock (and its resize splitter) off-screen.
        self._center_on_screen()

        # Open BTCUSDT by default (cache-first; on the main thread per the data-layer
        # thread-safety constraint), so the app starts on a populated chart.
        QtCore.QTimer.singleShot(200, lambda: self._load_symbol("BTCUSDT"))

    def _center_on_screen(self) -> None:
        """Clamp the window to the available screen area and center it."""
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        self.resize(min(self.width(), avail.width()), min(self.height(), avail.height()))
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())

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
        # Rounded chart "card": pyqtgraph fills its viewport as a square rect, so we paint the
        # chart on a TRANSPARENT scene/viewport inside a rounded card whose CHART_BG shows
        # through at the corners (anti-aliased). FullViewportUpdate avoids any repaint trails
        # from the translucent viewport.
        self.price.setBackground(None)
        _vp = self.price.viewport()
        _vp.setAutoFillBackground(False)
        _vp.setStyleSheet("background:transparent;")
        self.price.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
        chart_card = QtWidgets.QWidget()
        chart_card.setObjectName("chartCard")
        # subtle border so the rounded corners read against the near-black canvas (the chart
        # bg and canvas are both near-black, so without an outline the curve is invisible).
        chart_card.setStyleSheet(
            f"#chartCard{{background:{theme.CHART_BG};border:1px solid {theme.BORDER};"
            f"border-radius:16px;}}"
        )
        _card_lay = QtWidgets.QVBoxLayout(chart_card)
        _card_lay.setContentsMargins(0, 0, 0, 0)
        # vertical splitter inside the card: price chart on top, oscillator panes stacked below
        _price_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        _price_split.setHandleWidth(6)
        _price_split.addWidget(self.price)
        _price_split.setStretchFactor(0, 1)
        self.price.set_pane_host(_price_split)
        _card_lay.addWidget(_price_split)

        charts = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        charts.addWidget(chart_card)
        charts.setStretchFactor(0, 1)

        container = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(container)
        # generous padding so the chart "floats" with gaps to the rail / docks / edges (vike.io look)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)
        outer.addWidget(charts, 1)

        # The Chart space (clean price chart) and the Studio (AI strategy dev) are sibling
        # tabs of one window. The replay/data control bar and the Bots panel now live in the
        # Studio workspace (moved out of the Chart space and the right dock respectively).
        self._backtester = container
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(container, "Chart")
        self.studio = StudioTab()
        self._wire_studio_agent()
        # Bots panel (Active Bots / Historic Runs / Launch Bot) intentionally NOT mounted in
        # Studio for now — pending a refactor. self.bots stays alive so self.strategy /
        # self.history (its sub-widgets) keep serving show_strategy()/update_runs() calls.
        #
        # Replay/data controls: a 2-column button strip docked to the chart's RIGHT (fitted to
        # its height), with the scrubber on a full-width row BELOW the chart.
        _controls, _scrubber = self._build_controls()
        _chart_block = QtWidgets.QWidget()
        _cb = QtWidgets.QVBoxLayout(_chart_block)
        _cb.setContentsMargins(0, 0, 0, 0)
        _cb.setSpacing(4)
        _row = QtWidgets.QHBoxLayout()
        _row.setContentsMargins(0, 0, 0, 0)
        _row.setSpacing(6)
        # studio chart + its own stacked oscillator sub-panes
        _studio_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        _studio_split.setHandleWidth(6)
        _studio_split.addWidget(self.studio_price)
        _studio_split.setStretchFactor(0, 1)
        self.studio_price.set_pane_host(_studio_split)
        _row.addWidget(_studio_split, 1)
        _row.addWidget(_controls, 0)
        _cb.addLayout(_row, 1)
        _cb.addWidget(_scrubber)
        self.studio.mount_chart(_chart_block)
        self.tabs.addTab(self.studio, "Studio")
        # Tools tab hidden per user request (see import note above).
        # self.tools = ToolsTab()
        # self.tabs.addTab(self.tools, "Tools")
        self.screener = ScreenerTab()
        self.tabs.addTab(self.screener, "Screener")
        self.journal = JournalTab()
        self.tabs.addTab(self.journal, "Journal")
        self.alerts = AlertsTab()
        self.tabs.addTab(self.alerts, "Alerts")
        self.datamanager = DataManagerTab(pins_path=_PINS_PATH)
        self.tabs.addTab(self.datamanager, "Data")
        self.news = NewsTab()
        self.tabs.addTab(self.news, "News")
        self.economic_calendar = EconomicCalendarTab()
        self.calendar_space = CalendarSpace(economic_tab=self.economic_calendar)
        self.tabs.addTab(self.calendar_space, "Calendar")
        self.options = OptionsTab()
        self.tabs.addTab(self.options, "Options")
        self._options_svc = OptionsService(parent=self)
        self._options_started = False
        self._wire_options()

        # The left icon rail is the PRIMARY navigation (TradeLocker-style) — the horizontal
        # tab strip is hidden, so the rail alone switches between the spaces.
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
        # No frame line: the default toolbar border drew a stray horizontal rule under the
        # title bar. Match the rail to the vike canvas so it reads as one flush sidebar.
        rail_tb.setStyleSheet(f"QToolBar{{border:none;background:{theme.BG};}}")
        rail_tb.addWidget(self._build_icon_rail())
        self.addToolBar(QtCore.Qt.LeftToolBarArea, rail_tb)
        self.tabs.currentChanged.connect(self._on_space_changed)
        self._rail_tb = rail_tb  # anchor for the rail separator line (see _place_rules)

        # Full-width separator hairlines (under the title bar + above the status bar), painted
        # in device-pixel space by one overlay so they match exactly at any display scaling.
        # The top one also replaces the rail's old vertical border.
        self._rules = _RuleOverlay(self)
        self._rules.raise_()

    def _wire_options(self) -> None:
        """Connect the Options tab <-> service. Fetching only starts when the tab is first
        shown (keeps startup + headless tests network-free). One expiry at a time: the tab
        strip picks it; the service fetches and polls just that expiry (Deribit-style)."""
        tab, svc = self.options, self._options_svc
        svc.chainReady.connect(tab.set_chain)     # single-expiry flat view
        svc.failed.connect(tab.set_status)
        self._options_all_expiries: list = []
        self._options_expiry = None               # the selected expiry (None until one is picked)

        def _filtered() -> list:
            days = tab.exp_range_days()
            within = [e for e in self._options_all_expiries if days is None or e.dte <= days]
            return within or self._options_all_expiries

        def _on_expiries(expiries) -> None:
            self._options_all_expiries = list(expiries)
            tab.set_expiries(_filtered())   # the strip auto-selects the nearest -> _select fires

        svc.expiriesReady.connect(_on_expiries)

        def _load_underlying(sym: str) -> None:
            svc.stop_polling()
            self._options_expiry = None
            svc.set_underlying(sym)
            svc.set_strikes(tab.strikes_value())
            svc.load_expiries()

        def _select(iso: str) -> None:
            expiry = next((e for e in self._options_all_expiries if e.date == iso), None)
            if expiry is None:
                return
            self._options_expiry = expiry
            svc.stop_polling()
            svc.set_expiry(expiry)
            svc.set_strikes(tab.strikes_value())
            svc.start_polling()             # single-expiry poll: refresh() -> chainReady

        def _refresh() -> None:
            svc.set_strikes(tab.strikes_value())
            svc.refresh()

        tab.underlyingChanged.connect(_load_underlying)
        tab.expiryChanged.connect(_select)
        tab.rangeChanged.connect(lambda: tab.set_expiries(_filtered()))
        tab.refreshRequested.connect(_refresh)
        self._load_options_underlying = _load_underlying

    def _maybe_start_options(self) -> None:
        if not self._options_started:
            self._options_started = True
            self._load_options_underlying(self.options.underlying.currentText())
        elif self._options_expiry is not None:
            self._options_svc.start_polling()  # resume the selected expiry's poll on re-open

    # Order MUST match the addTab() order in _build_central — rail buttons map to tab
    # index by position here. Append new spaces last to keep existing indices stable.
    _RAIL_ITEMS = [
        ("▤", "Chart"), ("✦", "Studio"),  # ("⚙", "Tools") — hidden per user request
        ("⊞", "Screener"), ("☰", "Journal"), ("◉", "Alerts"), ("◈", "Data"),
        ("📰", "News"), ("▦", "Calendar"), ("⊗", "Options"),
    ]

    # PANELS section of the rail: independent show/hide toggles (TradeLocker style).
    # (key, icon_name, tooltip, shortcut). "backtester" toggles the centre chart; the others
    # map to docks in _panel_dock_map.
    _PANELS = [
        ("backtester", "chart", "Chart", "Ctrl+G"),
        ("market", "market", "Market watch", "Ctrl+M"),
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
        rail.setFixedWidth(62)
        # Sidebar = the vike canvas colour; no right border (removed per the user — the
        # separator is the full-width horizontal rule under the title bar instead).
        rail.setStyleSheet(f"background:{theme.BG};")
        col = QtWidgets.QVBoxLayout(rail)
        col.setContentsMargins(8, 10, 8, 10)
        col.setSpacing(6)

        # No brand mark or "SPACES" caption — the V lives in the OS title bar, and the rail opens
        # straight onto the space icons (cleaner; no caption + no rule under the title bar).
        self._rail_group = QtWidgets.QButtonGroup(self)
        self._rail_group.setExclusive(True)
        # No filled box behind icons: the active icon is shown by its green colour alone
        # (transparent background), per the user. Hover stays a faint colour-only cue.
        btn_qss = (
            f"QToolButton{{background:transparent;border:none;border-radius:13px;"
            f"color:{theme.TEXT3};font-size:22px;}}"
            f"QToolButton:hover{{background:transparent;color:{theme.TEXT2};}}"
            f"QToolButton:checked{{background:transparent;color:{theme.ACCENT};}}"
        )
        for i, (glyph, name) in enumerate(self._RAIL_ITEMS):
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(name.lower(), theme.TEXT3, theme.ACCENT, theme.TEXT2))
            b.setIconSize(QtCore.QSize(28, 28))
            b.setToolTip(self._chip_tip(name))
            b.setCheckable(True)
            b.setFixedSize(46, 46)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            b.clicked.connect(lambda _c, idx=i: self.tabs.setCurrentIndex(idx))
            self._rail_group.addButton(b, i)
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        col.addStretch(1)

        # PANELS toggles — independent show/hide for the three docks (TradeLocker-style).
        # Wired to the docks in _wire_panels_toggle() once _build_docks() has created them.
        # (Section caption removed per the user; the toggles sit directly under the spaces.)
        self._panel_btns: dict[str, QtWidgets.QToolButton] = {}
        for key, icon_name, tip, sc in self._PANELS:
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(icon_name, theme.TEXT3, theme.ACCENT, theme.TEXT2))
            b.setIconSize(QtCore.QSize(28, 28))
            b.setToolTip(self._chip_tip(tip, sc))
            b.setCheckable(True)
            b.setChecked(True)
            b.setFixedSize(46, 46)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            self._panel_btns[key] = b
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        col.addSpacing(6)

        first = self._rail_group.button(0)
        if first is not None:
            first.setChecked(True)
        return rail

    def _on_space_changed(self, index: int) -> None:
        """Lazy-start the news feed the first time the News space is opened."""
        if self.tabs.widget(index) is getattr(self, "news", None):
            self.news.start_feed(self._symbol)

    def _build_statusbar(self) -> QtWidgets.QStatusBar:
        sb = QtWidgets.QStatusBar()
        sb.setSizeGripEnabled(False)
        # No CSS border here — the top separator is an overlay QFrame (see _place_rules), so the
        # bottom one is too, guaranteeing both render at exactly the same 1px thickness.
        sb.setStyleSheet(
            f"QStatusBar{{background:{theme.CHART_BG};border:none;}}"
            f"QStatusBar::item{{border:none;}}"
        )
        # "Loaded"/"Ready" status text removed from the bar per the user; keep the label as a
        # hidden sink so the existing foot_status.setText(...) calls (and tests) still work.
        self.foot_status = QtWidgets.QLabel("Ready")
        self.foot_status.hide()

        self.foot_info = QtWidgets.QLabel("No data loaded")
        self.foot_info.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;padding:0 6px;")
        sb.addPermanentWidget(self.foot_info)
        self._feed_badge = QtWidgets.QLabel("● BINANCE")
        # no pill/border per the user — just the green dot + label, flush on the bottom bar
        self._feed_badge.setStyleSheet(
            f"color:{theme.UP};font-size:10px;background:transparent;"
            f"border:none;padding:3px 6px;margin-right:6px;"
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
        """Back-compat shim: route the old badge call through the health-based renderer."""
        self._set_feed_health("live" if live else "idle")

    def _set_feed_health(self, state: str) -> None:
        """Paint the feed badge for ``state`` and re-tune the live poll cadence.

        live → steady base cadence; stale → slow 30s poll (quiet/closed market); down →
        exponential backoff capped at 30s (REST is stateless, so the feed self-recovers).
        Replaces the old static "● BINANCE" label, which was a provider name wearing a
        "connected" disguise — it never reflected whether data was actually flowing.
        """
        color, prefix = _FEED_STATES.get(state, _FEED_STATES["idle"])
        self._feed_badge.setText(f"{prefix}{self._feed_label(self._symbol)}")
        self._feed_badge.setStyleSheet(
            f"color:{color};font-size:10px;background:transparent;border:none;"
            f"padding:3px 6px;margin-right:6px;"
        )
        if not self._live_timer.isActive():
            return
        if state == "live":
            self._live_timer.setInterval(self._live_base_ms)
        elif state == "stale":
            self._live_timer.setInterval(30_000)
        else:  # down -> back off, but keep retrying
            self._live_timer.setInterval(min(self._live_base_ms * 2 ** self._live_fail_streak, 30_000))

    def _update_feed_health(self) -> None:
        """Classify the live feed (data freshness + failure streak) and repaint the badge."""
        if self._forward is not None:
            self._set_feed_health("live")  # forward mode runs its own (genuinely live) feed
            return
        now = int(time.time() * 1000)
        newest = self._bars[-1].ts if self._bars else None
        self._set_feed_health(
            feed_health(now, newest, interval_ms(self._interval), self._live_fail_streak)
        )

    def _arm_live_updates(self) -> None:
        """(Re)start the live chart updater for the current symbol/interval (idempotent).

        Cadence polls a few times per candle (≈5s for 1m), capped at 10s. Recorded-run and
        hand-loaded views call ``_stop_live_updates`` instead, so live polling stays scoped to
        the live-symbol path.
        """
        if self._forward is not None:
            return
        self._live_fail_streak = 0
        self._live_base_ms = max(2_000, min(interval_ms(self._interval) // 12, 10_000))
        self._live_timer.start(self._live_base_ms)
        self._update_feed_health()
        # Poll promptly on arm at the base cadence even if the loaded cache reads STALE: otherwise
        # _update_feed_health would have just slowed the very first poll to the 30s STALE cadence,
        # leaving a freshly-loaded chart sitting STALE for 30s before it catches up to the edge.
        self._live_timer.setInterval(self._live_base_ms)

    def _stop_live_updates(self) -> None:
        self._live_timer.stop()
        self._set_feed_health("idle")

    def _live_tick(self) -> None:
        """Spawn an off-thread fetch of the latest bars for the live symbol (non-blocking).

        No-ops in forward mode (that feed owns the network) or while a fetch is already in
        flight. The fetch runs on a worker thread so the poll never stutters the UI; the merge
        and repaint happen back on the main thread in ``_on_live_fetched``.
        """
        if self._forward is not None or not self._bars:
            return
        now = int(time.time() * 1000)
        # Skip only while a fetch is *genuinely* in flight. A worker running longer than the
        # timeout is presumed stuck (hung connection / a 'finished' we never saw) — abandon it and
        # fetch afresh, so one bad fetch can't freeze the feed at STALE forever.
        if fetch_in_flight(self._live_worker, self._live_worker_started, now, _LIVE_FETCH_TIMEOUT_MS):
            return
        symbol, interval = self._symbol, self._interval
        step = interval_ms(interval)
        # Gap-aware: normally the last few bars, but stretch back to bridge a pause (e.g. after
        # a long Forward run) so a returning session doesn't tear a hole in the series.
        start, end = live_fetch_window(self._bars[-1].ts, now, step, lookback=_LIVE_LOOKBACK)
        self._live_fetch_for = (symbol, interval)
        worker = self._live_worker = _LiveFetchWorker(
            select_source(symbol).fetch_bars_range, symbol, interval, start, end
        )
        self._live_worker_started = now
        worker.fetched.connect(self._on_live_fetched)
        worker.failed.connect(self._on_live_fetch_failed)
        worker.finished.connect(lambda w=worker: self._clear_live_worker(w))
        worker.start()

    def _clear_live_worker(self, worker=None) -> None:
        # Only the *current* worker clears the guard — a late-finishing abandoned worker must not
        # clear a fetch we've since started.
        if worker is None or worker is self._live_worker:
            self._live_worker = None

    def _on_live_fetched(self, fetched) -> None:
        """Main thread: merge the fetched bars and repaint the live edge (if we're viewing it)."""
        # Discard if state moved on while the fetch ran (symbol/interval switched, or Forward began).
        if self._forward is not None or not self._bars:
            return
        if self._live_fetch_for != (self._symbol, self._interval):
            return
        self._live_fail_streak = 0
        merged, appended, replaced_last = merge_live_bars(self._bars, fetched)
        if appended or replaced_last:
            was_at_end = self._replay.at_end
            self._bars = merged
            self._replay.n_bars = len(merged)
            self.slider.setMaximum(self._replay.last_index)
            overlays = self._strategy_factory().chart_overlays([b.close for b in merged])
            for ch in (self.price, self.studio_price):
                ch.apply_live(merged, overlays, repaint=False)
            if was_at_end:  # following the live edge -> advance the cursor and repaint
                self._replay.seek(self._replay.last_index)
                self._render_frame()
                self.foot_info.setText(
                    f"{self._symbol} · {self._interval} · {len(merged):,} bars"
                )
        self._update_feed_health()

    def _on_live_fetch_failed(self, _message: str) -> None:
        """Main thread: a transient fetch failure -> watchdog backs off and keeps retrying.

        Silent by design (no modal): this fires off a background timer, and a modal here would
        hang the headless UI test (see the ci-headless-hangs note).
        """
        self._live_fail_streak += 1
        self._update_feed_health()

    def _build_controls(self):
        """Replay/data controls for the Studio chart: a 2-column button strip docked at the
        chart's right (stretched to its height) + a full-width scrubber row the caller places
        below the chart. Returns ``(panel, scrubber)``."""
        self.btn_load = QtWidgets.QPushButton("⤓ Load data")
        self.btn_strategy = QtWidgets.QPushButton("⟐ Load strategy")
        self.btn_validate = QtWidgets.QPushButton("⚠ Validate")
        self.btn_validate.setObjectName("validate")
        self.btn_optimize = QtWidgets.QPushButton("⚙ Grid optimize")
        self.btn_optimize.setToolTip("Grid-search the loaded strategy's parameters (optimizer dialog)")
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

        # Single-column vertical button strip — docked to the right of the Studio chart,
        # stretching to the chart's height (buttons packed at the top).
        panel = QtWidgets.QWidget()
        col = QtWidgets.QVBoxLayout(panel)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        for w in (
            self.btn_load, self.btn_strategy,
            self.btn_validate, self.btn_optimize,
            self.btn_back, self.btn_play,
            self.btn_fwd, self.btn_full,
            self.btn_forward, self.speed,
        ):
            col.addWidget(w)
        col.addStretch(1)  # keep buttons packed at the top
        panel.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Expanding)

        # full-width scrubber row — the caller places this below the chart
        scrubber = QtWidgets.QWidget()
        srow = QtWidgets.QHBoxLayout(scrubber)
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        srow.addWidget(self.slider, 1)
        srow.addWidget(self.pos_label)
        return panel, scrubber

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
        #   Chart (centre) · Market watch (right) · Trades & Positions (full-width bottom).
        # The Bots panel now lives in the Studio workspace, not a right dock.
        # The bottom area owns both lower corners so the trades strip spans the full width,
        # with the side docks sitting above it.
        self.setCorner(QtCore.Qt.BottomLeftCorner, QtCore.Qt.BottomDockWidgetArea)
        self.setCorner(QtCore.Qt.BottomRightCorner, QtCore.Qt.BottomDockWidgetArea)

        market = self._dock("Market watch", self.watchlist)
        trades = self._dock("Trades & Positions", self._build_trades_panel())

        # RIGHT: Market watch.  BOTTOM: Trades & Positions, spanning the full width.
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, market)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, trades)

        # rail PANELS toggle targets (key must match _PANELS)
        self._market_dock = market
        self._trades_dock = trades
        self._panel_dock_map = {"market": market, "trades": trades}
        # Sizes used when the user OPENS a dock via the rail toggle (both start hidden on first
        # run — see _wire_panels_toggle, chart-first).
        self.resizeDocks([market], [300], QtCore.Qt.Horizontal)
        self.resizeDocks([trades], [190], QtCore.Qt.Vertical)
        self._docks = [market, trades]

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

        # Chart-first on first run: Market watch + Trades start CLOSED (not active/opened).
        # Un-checking the rail toggle hides the dock via the wiring above; the toggle (or its
        # Ctrl shortcut) re-opens it. Other panels (e.g. the chart) stay on.
        for _k in ("market", "trades"):
            if _k in self._panel_btns:
                self._panel_btns[_k].setChecked(False)

    def _toggle_panel(self, key: str, on: bool) -> None:
        self._panel_visible[key] = on
        if self.tabs.currentWidget() is not self._backtester:
            return
        if key == "backtester":
            # Hide the centre (chart + controls); QMainWindow expands the docks to fill.
            self.tabs.setVisible(on)
        else:
            self._panel_dock_map[key].setVisible(on)

    def _refresh_pinned_tick(self) -> None:
        """Backstop timer: keep pinned rollups current (main thread — reads/writes Parquet).

        No-ops in forward mode (that owns the network); pure no-op when nothing is pinned.
        Errors are swallowed (no modal in a timer path — see the headless-CI hang note).
        """
        if self._forward is not None:
            return
        try:
            refresh_pinned(DEFAULT_ROOT, load_pins(_PINS_PATH))
        except Exception:  # noqa: BLE001 - transient read/write; retried next tick
            pass

    def _on_tab_changed(self, index: int) -> None:
        """Show the Chart docks only on the Chart tab (internally still keyed `backtester`); Studio/Tools are full-width."""
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
        # Options space: start fetching on first open; pause polling when navigating away.
        if getattr(self, "options", None) is not None:
            if self.tabs.currentWidget() is self.options:
                self._maybe_start_options()
            elif getattr(self, "_options_started", False):
                self._options_svc.stop_polling()

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
        overlays = self._strategy_factory().chart_overlays([b.close for b in bars])
        for ch in (self.price, self.studio_price):
            ch.set_data(bars, self._result.trades)
            ch.set_overlays(overlays)
            ch.set_title(self._symbol)  # symbol-only; far-left toolbar label (no "· interval")
            ch.set_timeframe(self._interval)
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
        self._update_feed_health()
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
        # backtests are closed-bar-only: drop the still-forming live candle if present.
        bars = closed_bars(self._bars, interval_ms(self._interval), int(time.time() * 1000))
        self.load_bars(bars, record=True)

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
        self._stop_live_updates()  # a recorded run is a fixed past window, not the live edge

    def _open_load_dialog(self):
        dlg = LoadDataDialog(self)
        if dlg.exec() == QtWidgets.QDialog.Accepted and dlg.bars:
            self._symbol = dlg.symbol.text().strip() or self._symbol
            self._interval = dlg.interval.currentText()
            self.load_bars(dlg.bars)
            self._stop_live_updates()  # a hand-loaded dataset isn't the live symbol feed

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

    def _push_watch_quote(self, symbol, bars) -> None:
        """Update one watchlist row's quote from freshly loaded bars (keeps it in sync)."""
        quote = quote_from_bars(bars)
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
                quote = quote_from_bars(self._price_cat.query(sym, "1m", start, now))
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

    def _load_symbol(self, symbol, interval=None):
        """Load ``symbol`` at ``interval`` (default: current, else 1m), topping up the recent gap.

        Cache-first: a *fresh* cached tail (newest bar within ``_WATCHLIST_FRESH_MS``) paints
        instantly with zero network. Otherwise we fetch only the missing recent gap before
        painting — ``get_bars`` is incremental, so it pulls just the bars after the last cached
        one, never a full re-download. (The old logic served deep-but-hours-stale history
        without ever fetching the gap, which is why the chart lagged behind Binance.) If the
        top-up fetch fails we fall back to whatever is cached rather than leaving an empty chart.
        """
        if hasattr(self, "news"):
            self.news.set_symbol(symbol)
        interval = interval or getattr(self, "_interval", None) or "1m"
        now = int(time.time() * 1000)
        start = now - _INTERVAL_LOOKBACK.get(interval, _WATCHLIST_DAYS) * _DAY_MS

        from ..data.catalog import Catalog
        cached = Catalog().query(symbol, interval, start, now)
        if cached and not is_stale(cached[-1].ts, now, _WATCHLIST_FRESH_MS):
            self._symbol, self._interval = symbol, interval     # fresh -> paint instantly
            self.load_bars(cached)
            self._push_watch_quote(symbol, cached)
            self._arm_live_updates()
            return

        self.crumb.setText(f"Loading {symbol}…")
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            bars = get_bars(symbol, interval, start, now, progress=self._fetch_progress,
                            fetcher=select_source(symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - network/load failure
            if cached:  # offline / fetch failed -> show cached rather than nothing
                self._symbol, self._interval = symbol, interval
                self.load_bars(cached)
                self._push_watch_quote(symbol, cached)
                self.crumb.setText(f"{symbol}: latest unavailable, showing cached · {exc}")
                self._arm_live_updates()  # keep retrying; the watchdog shows reconnecting/stale
                return
            # Report to the status line, NOT a modal: this path runs from the startup auto-load
            # and the watchlist, where a modal would block a headless/CI event loop (no user to
            # dismiss it) — the cause of the CI ui-test hang.
            self.crumb.setText(f"{symbol}: load failed · {exc}")
            if hasattr(self, "foot_info"):
                self.foot_info.setText(f"{symbol} · load failed")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._symbol = symbol
        self._interval = interval
        self.load_bars(bars)
        self._push_watch_quote(symbol, bars)
        self._arm_live_updates()

    def _on_interval_chosen(self, interval: str):
        """Timeframe dropdown -> reload the current symbol at the chosen interval."""
        if getattr(self, "_symbol", None):
            self._load_symbol(self._symbol, interval)

    def _add_pairs(self, chart, name: str):
        """Prompt for a 2nd symbol, fetch + align its closes to ``chart``'s bars, and add the
        pairs indicator (ratio/spread/zscore) as an oscillator pane on that chart."""
        bars = chart._bars
        if not bars:
            return
        sym, ok = QtWidgets.QInputDialog.getText(
            self, "Compare symbol", "Second symbol for the pair:", text="ETHUSDT"
        )
        if not ok or not sym.strip():
            return
        sym = sym.strip().upper()
        start, end = bars[0].ts, bars[-1].ts
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            from ..data.catalog import Catalog

            bench = Catalog().query(sym, self._interval, start, end)
            if not bench:  # not cached -> fetch the gap (main thread; data layer isn't thread-safe)
                bench = get_bars(sym, self._interval, start, end,
                                 fetcher=select_source(sym).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - network/load failure
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Compare failed", f"{sym}: {exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        if not bench:
            QtWidgets.QMessageBox.information(self, "Compare", f"No data for {sym} in this range.")
            return
        # align the benchmark closes to the chart bars by timestamp (forward-filled gaps)
        bench_close = {b.ts: b.close for b in bench}
        series, last = [], None
        for b in bars:
            v = bench_close.get(b.ts)
            if v is not None:
                last = v
            series.append(last)
        first = next((v for v in series if v is not None), None)
        if first is None:
            QtWidgets.QMessageBox.information(self, "Compare", f"No overlapping data for {sym}.")
            return
        chart.add_pairs(name, [v if v is not None else first for v in series])

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
        for ch in (self.price, self.studio_price):
            ch.show_upto(i)
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
        overlays = self._strategy_factory().chart_overlays([b.close for b in self._fwd_bars])
        for ch in (self.price, self.studio_price):
            ch.set_data(self._fwd_bars, res.trades)
            ch.set_overlays(overlays)
            ch.show_upto(len(self._fwd_bars) - 1)
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
        self._set_backtest_controls_enabled(True)
        # Resume live polling for the symbol; A1's gap-aware fetch bridges the Forward pause so
        # the chart returns to a current, continuous live view (no modal — runs off a timer).
        self._arm_live_updates()

    def _set_backtest_controls_enabled(self, on: bool):
        """Lock backtest/replay controls while forward mode owns the charts (and vice-versa)."""
        for w in (
            self.btn_load, self.btn_strategy, self.btn_validate, self.btn_optimize,
            self.btn_back, self.btn_play, self.btn_fwd, self.btn_full, self.slider, self.speed,
        ):
            w.setEnabled(on)

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self._apply_titlebar_color()  # native caption colour needs a live HWND (post-show)
        self._place_rules()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place_rules()

    def _place_rules(self) -> None:
        """Size the separator overlay to the whole window and tell it where the bottom rule
        goes (the top of the status bar). The overlay paints both lines in device pixels."""
        overlay = getattr(self, "_rules", None)
        if overlay is None:
            return
        overlay.setGeometry(0, 0, self.width(), self.height())
        sb = self.statusBar()
        bottom_y = sb.geometry().top() if sb is not None else self.height() - 1
        rail = getattr(self, "_rail_tb", None)
        vline_x = rail.geometry().right() if rail is not None else 0  # rail's right edge
        overlay.set_lines(bottom_y, vline_x)
        overlay.raise_()

    def _apply_titlebar_color(self) -> None:
        """Recolour the native Windows 11 title bar to match the chart (DWM caption color).

        Windows draws the caption itself, so we set it via DwmSetWindowAttribute
        (CAPTION_COLOR/TEXT_COLOR/BORDER_COLOR — Win11 22000+). No-op elsewhere.
        """
        import sys

        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes

            def _colorref(hex_color: str) -> int:  # COLORREF = 0x00BBGGRR
                r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
                return (b << 16) | (g << 8) | r

            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            for attr, color in ((35, theme.CHART_BG),  # DWMWA_CAPTION_COLOR
                                (36, theme.TEXT),       # DWMWA_TEXT_COLOR
                                (34, theme.CHART_BG)):  # DWMWA_BORDER_COLOR
                val = wintypes.DWORD(_colorref(color))
                dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), wintypes.DWORD(attr),
                                          ctypes.byref(val), ctypes.sizeof(val))
        except Exception:  # noqa: BLE001 - older Windows / no DWM -> keep the default caption
            pass

    def closeEvent(self, event):  # noqa: N802 - Qt override
        self._stop_forward()  # never leave a feed thread running
        if hasattr(self, "news"):
            self.news.stop_feed()  # halt the news poller thread
        self.studio.shutdown()  # wait out any in-flight AI worker (no destroyed-while-running)
        if getattr(self, "_options_svc", None) is not None:
            self._options_svc.shutdown()  # stop the poll + wait out any options fetch worker
        if getattr(self, "_price_timer", None) is not None:
            self._price_timer.stop()  # halt the watchlist price-fill ticks
        if getattr(self, "_refresh_timer", None) is not None:
            self._refresh_timer.stop()  # halt the live quote refresh
        super().closeEvent(event)

    def _tick_clock(self):
        self.clock.setText(QtCore.QTime.currentTime().toString("HH:mm:ss"))


def _install_qt_log_filter():
    """Silence known-benign Qt warnings (missing bundled fonts dir, platform size-hint
    notice) while letting every other Qt message through to stderr."""
    import sys

    _benign = ("Cannot find font directory", "propagateSizeHints")

    def handler(mode, ctx, msg):  # noqa: ANN001
        if any(s in msg for s in _benign):
            return
        sys.stderr.write(msg + "\n")

    QtCore.qInstallMessageHandler(handler)


def main():
    import sys

    try:  # honor .env so API keys / options-backend config are picked up (python-dotenv is a core dep)
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - missing .env / dotenv must never block launch
        pass

    _install_qt_log_filter()
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
