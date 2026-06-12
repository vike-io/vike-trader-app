"""The vike-trader-app desktop app: a visual backtester in the vike.io look.

Dockable layout (QDockWidget): Markets + Strategy on the left, the candle/equity
charts and replay bar in the centre, Backtest Report + Trades on the right, with a
full-width header. The "⚠ Validate" button runs the anti-overfit report and lights
up the verdict banner — the differentiator.
"""

import json
import os
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
import PySide6QtAds as QtAds

from . import icons, theme
from .bots_panel import BotsPanel
from .chart import PriceChart
from .chartdoc import ChartDocument, LiveHub
from .dialogs import LoadDataDialog, default_strategy_factory
from .dockshell import SpaceDeck, configure_dock_manager_defaults, dock_qss, make_panel_dock
from .linkbus import LINK_COLOR, SymbolLinkBus
from .panels import (
    LinkDot,
    TradesTable,
    WatchlistPanel,
    strategy_params,
)
from .replay import Replay
from .session import (
    SessionState,
    apply_indicator_states,
    indicator_states,
    load_session,
    save_session,
)
from .watchlist_data import is_stale, quote_from_bars
from .workspaces import WorkspaceStore
from .studio import StudioTab
# The 7 non-Studio tool widgets (AlertsTab, JournalTab, NewsTab, ScreenerTab, DataManagerTab,
# EconomicCalendarTab/CalendarSpace, OptionsTab) are NO LONGER imported here: they are built
# on demand by ToolRegistry (ui/toolreg.py) inside open_tool(), not eagerly in _build_central.
# (Tools tab of standalone calculators stays hidden — restore via its addTab + a _TOOL_ITEMS entry.)
from ..data.options.service import OptionsService  # app-level OptionsService stays eager (Plan 1)

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
    "cached": (theme.TEXT3, "● CACHED · "),  # data on screen, but no live poller is running
    "idle": (theme.TEXT3, "● "),
}
_DB_PATH = "storage/db/vike_trader_app.sqlite"
_PINS_PATH = "storage/pins.json"  # pinned (symbol, interval) series kept precomputed (rollups)
# Empty-workspace re-arch: the 7 non-Studio tools open as on-demand docks (open_tool). While a
# tool dock is open, open_tool mirrors the live instance onto the legacy MainWindow attribute below
# (and the calendar tool's CalendarSpace onto self.calendar_space) so existing readers keep working;
# the dock-close handler clears it back to None. The tool *key* differs from the attr only for
# data->datamanager and calendar->calendar_space. "studio" is the 8th on-demand tool now (its attr
# is self.studio; the close handler ALSO nils self.studio_price and runs studio.shutdown()).
_TOOL_ATTR = {"screener": "screener", "journal": "journal", "alerts": "alerts",
              "data": "datamanager", "news": "news", "calendar": "calendar_space",
              "options": "options", "studio": "studio"}
_SESSION_PATH = "storage/session.json"  # last-session snapshot (geometry/space/symbol/indicators)
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


class _WorkspaceAgentWorker(QtCore.QThread):
    """Off-thread run of the layout agent (a Claude API call). Emits the captured spec dict (or
    None) back to the main thread, where the shell converts + applies it."""

    done = QtCore.Signal(object)  # spec dict | None

    def __init__(self, client, prompt, parent=None):
        super().__init__(parent)
        self._client, self._prompt = client, prompt

    def run(self):
        try:
            from ..ai.workspace import develop_workspace
            spec = develop_workspace(self._prompt, client=self._client)
        except Exception:  # noqa: BLE001 - network/SDK failure -> no layout (reported by caller)
            spec = None
        self.done.emit(spec)


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

    def __init__(self, session_path: str | None = _SESSION_PATH):
        super().__init__()
        self.setWindowTitle(f"vike-trader-app   {self._SPACE_ITEMS[0][1]}")  # space name updated on tab change
        self.setWindowIcon(icons.brand_icon(theme.ACCENT, theme.BG))  # brand V in the title bar
        self.resize(1440, 900)
        self.setDockNestingEnabled(True)
        self.setDockOptions(
            QtWidgets.QMainWindow.AnimatedDocks | QtWidgets.QMainWindow.AllowNestedDocks
        )

        # Session restore. The env kill-switch applies only to the DEFAULT path: the offscreen
        # test suite sets it (tests/conftest.py) so a developer's local session file can't leak
        # state into tests that construct MainWindow() bare — while session tests still exercise
        # persistence by passing an explicit tmp path.
        if session_path == _SESSION_PATH and os.environ.get("VIKE_DISABLE_SESSION"):
            session_path = None
        self._session_path = session_path
        self._session = load_session(session_path) if session_path else None

        self._bars = []
        self._result = None
        self._replay = Replay(0)
        self._strategy_factory = default_strategy_factory()
        self._symbol = self._session.symbol if self._session else "BTCUSDT"
        self._interval = self._session.interval if self._session else "1m"

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
        # Studio is now a closable/hideable on-demand DOCK (the 8th tool), not an eager space:
        # self.studio (StudioTab) + self.studio_price (its lockstep PriceChart) are built lazily by
        # _build_studio_widget when the Studio dock opens, and re-nilled when it closes. Every
        # backtest/replay/live pipeline site reads them through guards (_pipeline_charts() for the
        # chart, getattr-None checks for the tab), so the pipeline is safe while Studio is closed.
        self.studio = None
        self.studio_price = None
        self.trades = TradesTable()
        self.watchlist = WatchlistPanel()
        self.bots = BotsPanel()
        self.strategy = self.bots.strategy   # alias: existing code calls self.strategy.show_strategy
        self.history = self.bots.history     # alias: existing code calls self.history.update_runs
        self.store = Store(str(Path(_DB_PATH)))
        self.watchlist.symbolChosen.connect(self._load_symbol)
        self.watchlist.symbolChosen.connect(self._broadcast_watchlist_symbol)
        self.watchlist.openInNewChart.connect(self._open_in_new_chart)
        self.bots.runChosen.connect(self._open_run)
        self.bots.launchRequested.connect(self._launch_bot)

        # multi-instance chart documents (Phase 2): each is a standalone chart with its OWN
        # symbol/interval, tab-able next to the spaces and tear-out-able to a floating window.
        # LiveHub keeps the visible ones' live edges ticking with one round-robin fetch worker.
        self._live_hub = LiveHub(self)
        self._doc_seq = 0                  # monotonic id for stable dock objectNames (doc:N)
        self._doc_widgets: list[ChartDocument] = []
        self._chart_frames: list = []      # MC-style floating ChartWindowFrames (S7)
        self._active_frame = None
        # empty-workspace re-arch: open non-chart tools (screener/journal/… ) lazily as docks,
        # keyed by tool key for singleton open-or-focus (Plan 1). See ui/toolreg.py.
        self._tool_docks: dict[str, "QtAds.CDockWidget"] = {}

        self._layout_workers: list = []   # in-flight AI-layout agent threads (Phase 5)

        # symbol link groups (Phase 3): charts + the watchlist sharing a colour move together.
        self._link_bus = SymbolLinkBus()
        self._watchlist_link = self._session.watchlist_link if self._session else 0
        if self._watchlist_link not in LINK_COLOR:   # hand-edited / stale session -> unlinked
            self._watchlist_link = 0
        # The CENTRAL chart space is itself a link-group member (symbol ● + interval ◆ channels,
        # set via the dots on its header). The MainWindow proxies it: apply_link loads a received
        # symbol/interval; _broadcast_central_link pushes its own changes. Restored from session.
        self.link_group = getattr(self._session, "central_link", 0) if self._session else 0
        if self.link_group not in LINK_COLOR:
            self.link_group = 0
        _ivl = getattr(self._session, "central_interval_link", -1) if self._session else -1
        self.interval_link_group = None if _ivl < 0 else _ivl   # -1 sentinel = follow symbol
        self._link_bus.add_member(self)
        self._feed_state = "idle"        # current feed-health state; the header badge reads it
        self._header_feed = None         # the header's FeedBadge (recreated with the header)

        # named workspaces (Phase 4): persisted next to the session file; in-memory when the
        # session is disabled (offscreen tests) so a save never touches real storage.
        self._workspaces = WorkspaceStore(
            str(Path(self._session_path).with_name("workspaces.json"))
            if self._session_path else None
        )
        # timeframe dropdown on the Chart chart -> reload the current symbol at that interval.
        # (The Studio chart's intervalChosen/pairsRequested are wired in _build_studio_widget when
        # the Studio dock is built, since self.studio_price doesn't exist until then.)
        self.price.intervalChosen.connect(self._on_interval_chosen)
        # pairs indicators need a 2nd symbol the app fetches (the chart can't reach the data layer)
        self.price.pairsRequested.connect(lambda n: self._add_pairs(self.price, n))

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
        self.tabs.documentClosed.connect(self._on_document_closed)
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

        # Saved geometry restores the window where the user left it (Qt clamps it back
        # on-screen if the monitor layout changed). Without one, place the window fully
        # on-screen — Windows can otherwise position a 1440-wide window past the right
        # edge, pushing the Market watch dock (and its resize splitter) off-screen.
        self._restored_geometry = False
        if self._session and self._session.geometry_hex:
            try:
                self._restored_geometry = bool(self.restoreGeometry(
                    QtCore.QByteArray.fromHex(self._session.geometry_hex.encode("ascii"))
                ))
            except Exception:  # noqa: BLE001 - stale/garbled blob -> default placement
                self._restored_geometry = False
        if not self._restored_geometry:
            self._center_on_screen()

        # Recreate the last session's chart documents BEFORE restoreState, so their dock
        # objectNames (doc:0, doc:1, …) exist and the layout blob can position/float them 1:1.
        # They load cache-only here (no startup network storm) and top up when focused.
        if self._session and self._session.documents:
            for state in self._session.documents:
                self._new_chart_document(
                    state.get("symbol", "BTCUSDT"), state.get("interval", "1h"),
                    state=state, network=False, make_current=False,
                )

        # Recreate the last session's tool docks BEFORE restoreState, so their objectNames
        # (tool:<key>) exist and the layout blob positions them 1:1 — mirrors the chart-document
        # recreation above for PLACEMENT. Old sessions (no open_tools) recreate nothing → a
        # clean start.
        # NOTE: unlike chart docs (network=False), a restored News/Calendar dock starts its
        # HTTP feed immediately on open_tool — acceptable (HTTP, not the non-thread-safe data
        # layer) and reworked by Plan 2's shared feed hubs. VIKE_DISABLE_LIVE gates it in tests.
        if self._session and getattr(self._session, "open_tools", None):
            for key in self._session.open_tools:
                try:
                    self.open_tool(key)
                except Exception:  # noqa: BLE001 - one bad/stale tool key must not break launch
                    pass

        # Restore the dock layout (panel positions/sizes/pins, splitters). All dock widgets
        # exist by objectName at this point, so restoreState maps 1:1. Guarded as a
        # PROGRAMMATIC change: the layout blob reflects the close-time dock state (e.g. docks
        # force-closed off the Chart space) — letting its viewToggled bleed into the rail
        # toggles would overwrite the user's remembered per-panel intent (saved `panels`).
        if self._session and self._session.dock_state_hex:
            self._syncing_docks = True
            try:
                self.dock_manager.restoreState(
                    QtCore.QByteArray.fromHex(self._session.dock_state_hex.encode("ascii"))
                )
            except Exception:  # noqa: BLE001 - stale/garbled blob -> keep the default layout
                pass
            finally:
                self._syncing_docks = False
        # restoreState rebuilds tab widgets and re-shows the space tabs — re-hide them (the
        # rail is the space switcher; the center strip carries only chart documents).
        self.tabs.hide_space_tabs()

        # Reopen the last session's space. restoreState above can REBUILD the center dock area,
        # so SpaceDeck's currentChanged forward must be re-resolved and the shell re-synced
        # regardless of which space we land on — hence setCurrentIndex (which re-resolves) plus
        # an UNCONDITIONAL _on_tab_changed (rail check, title bar, per-space dock visibility).
        # The saved index is CLAMPED: a build that dropped/reordered a space (precedent: the
        # Tools space) could leave space >= count, which previously skipped the whole re-sync
        # and left the visible space disconnected from the rail until the first click.
        if self._session:
            space = self._session.space
            if not (0 <= space < self.tabs.count()):
                space = 0   # old sessions saved on a now-removed tool space -> Chart
            self.tabs.setCurrentIndex(space)
        self._on_tab_changed(self.tabs.currentIndex())

        # Open the last session's symbol (default BTCUSDT) cache-first, on the main thread
        # per the data-layer thread-safety constraint, so the app starts on a populated chart.
        QtCore.QTimer.singleShot(200, self._startup_load)

    def _startup_load(self) -> None:
        """Load the session symbol/interval, then re-apply each chart's saved indicators.

        Indicators only re-attach when the load actually produced bars (add_indicator
        no-ops on an empty chart) — a failed load just leaves a clean chart."""
        self._load_symbol(self._symbol, self._interval)
        if self._session and self._bars:
            apply_indicator_states(self.price, self._session.chart_indicators)
            # Studio chart only exists while its dock is open: a restored "studio" tool recreates
            # it (open_tools restore runs before this), otherwise studio_price is None — skip it.
            if self.studio_price is not None:
                apply_indicator_states(self.studio_price, self._session.studio_indicators)

    # --- chart documents (multi-instance, tear-out) -----------------------------------------
    def _new_chart_document(self, symbol: str, interval: str | None = None, *,
                            state: dict | None = None, network: bool = True,
                            make_current: bool = True) -> ChartDocument:
        """Open a chart WINDOW: an MC-style free-floating, overlapping frame over the workspace
        with its own title bar (drag/resize/roll-up/maximize/detach/close) — NOT a docked tab
        (the user explicitly rejected dock-tiling for charts).

        ``network=False`` loads cache-only (session restore — tops up on focus). ``state``
        re-attaches saved indicators/links (and geometry). Registered with the LiveHub so its
        live edge ticks while visible.
        """
        from .chartwin import ChartWindowFrame

        doc = ChartDocument(symbol, interval or self._interval)
        self._doc_widgets.append(doc)
        self._live_hub.register(doc)
        doc.set_bus(self._link_bus)        # join symbol link groups (colour set via its dot)

        frame = ChartWindowFrame(doc, self.dock_manager)
        frame.closed.connect(lambda f: self._on_chart_window_closed(f))
        frame.activated.connect(self._on_chart_window_activated)
        frame.cloneRequested.connect(self._clone_window)
        self._chart_frames.append(frame)
        # cascade placement: each new window steps down-right from the last
        n = len(self._chart_frames) - 1
        frame.move(36 + (n % 8) * 34, 24 + (n % 8) * 28)
        if state and isinstance(state.get("geometry"), list) and len(state["geometry"]) == 4:
            x, y, w, h = (int(v) for v in state["geometry"])
            frame.setGeometry(x, y, max(320, w), max(160, h))
        frame.show()
        doc.load(network=network)
        _fcolor, _fprefix = _FEED_STATES["live" if self._live_hub.is_live() else "cached"]
        frame.set_feed(_fcolor, _fprefix.replace(" · ", "").strip())
        if state:
            doc.apply_state(state)
        if make_current:
            frame.raise_()
            self._on_chart_window_activated(frame)
        return doc

    def _clone_window(self, frame) -> None:
        """Duplicate a chart window — same symbol / interval / indicators / link groups, cascaded
        to a fresh window. Reuses the copy/paste state capture (no clipboard round-trip)."""
        st = self._doc_state_with_geometry(frame.doc)
        st.pop("geometry", None)   # let the new window cascade instead of stacking exactly
        self._new_chart_document(st.get("symbol", frame.doc.symbol),
                                 st.get("interval", frame.doc.interval), state=st)

    def _open_central_as_window(self) -> None:
        """The chart-space header's ＋ : open the central chart's current view as a floating
        window (same symbol / interval / indicators / link group)."""
        self._new_chart_document(self._symbol, self._interval, state={
            "symbol": self._symbol, "interval": self._interval,
            "indicators": indicator_states(self.price),
            "link_group": self.link_group,
            "interval_link_group": self.interval_link_group,
        })

    def _frame_of(self, doc) -> "object | None":
        for f in self._chart_frames:
            if f.doc is doc:
                return f
        return None

    def _on_chart_window_closed(self, frame) -> None:
        if frame in self._chart_frames:
            self._chart_frames.remove(frame)
        self._on_document_closed(frame.doc)

    def _on_chart_window_activated(self, frame) -> None:
        self._active_frame = frame
        for f in self._chart_frames:
            f.set_active(f is frame)

    def _close_all_chart_windows(self) -> None:
        for f in list(self._chart_frames):
            f.close_window()

    def _arrange_chart_windows(self, mode: str) -> None:
        from . import chartwin

        chartwin.arrange(self._chart_frames, self.dock_manager, mode)

    def open_tool(self, key: str):
        """Open the tool dock for ``key``, or focus it if already open.

        SINGLETON (Plan 1): re-opening the same key raises and selects the existing dock
        instead of creating a second one. A later plan flips this to multi-instance — the
        open-or-focus shortcut is isolated in the leading branch so that flip is one edit.

        Studio is the 8th tool here: ToolRegistry's "studio" factory calls _build_studio_widget,
        which builds the StudioTab + its lockstep chart (self.studio / self.studio_price) on
        demand; the close handler below tears both down (shutdown + nil).
        """
        from .toolreg import ToolRegistry, make_tool_dock

        existing = self._tool_docks.get(key)
        if existing is not None and not existing.isClosed():
            existing.toggleView(True)
            area = existing.dockAreaWidget()
            if area is not None:
                area.setCurrentDockWidget(existing)
            existing.raise_()
            return existing
        widget = ToolRegistry.create(key, self)
        dock = make_tool_dock(
            self.dock_manager, key, widget,
            icon=icons.rail_icon(key, theme.TEXT3, theme.ACCENT, theme.TEXT2),
        )
        self.dock_manager.addDockWidget(QtAds.CenterDockWidgetArea, dock)
        self._tool_docks[key] = dock
        # Mirror the live instance onto its legacy attribute so existing readers (signals,
        # set_symbol, dashboard-tile seeding, …) keep working while the tool is open.
        attr = _TOOL_ATTR.get(key)
        if attr:
            setattr(self, attr, widget)
        self._wire_tool(key, widget)

        def _on_tool_closed(k=key, a=attr, w=widget):
            self._tool_docks.pop(k, None)
            # Studio (the 8th tool) carries an AI worker + a lockstep chart fused to the pipeline:
            # wait the worker out (no destroyed-while-running) and rescue the eager replay controls
            # out of the DeleteOnClose dock tree BEFORE it is torn down, then nil studio_price so
            # _pipeline_charts() drops it. (studio_price itself lives in the dock tree -> destroyed.)
            if k == "studio":
                if hasattr(w, "shutdown"):
                    try:
                        w.shutdown()
                    except Exception:  # noqa: BLE001 - teardown best-effort; never block the close
                        pass
                self._rescue_studio_controls()
                self.studio_price = None
            if a and getattr(self, a, None) is w:
                setattr(self, a, None)   # clear the legacy alias (no dangling ref to a dead widget)
            # Stop any per-tool background work on dock close so no poller thread leaks.
            if hasattr(w, "stop_feed"):
                try:
                    w.stop_feed()       # News poller thread
                except Exception:  # noqa: BLE001 - teardown best-effort; never block the close
                    pass
            if k == "options" and getattr(self, "_options_svc", None) is not None:
                self._options_svc.stop_polling()   # the poller lives on the app-level service,
                self._options_started = False      # not the tab, so stop it here (re-arm next open)
                # The tab is about to be destroyed (DeleteOnClose); an in-flight _FetchWorker
                # QThread could still emit into it (→ "C++ object already deleted" / 0xC0000409).
                # Drop the svc->tab connections NOW, not on the next open (too late). Re-armed by
                # _wire_options on the next open via the _options_wired guard below.
                for _sig in (self._options_svc.chainReady, self._options_svc.failed,
                             self._options_svc.expiriesReady):
                    try:
                        _sig.disconnect()
                    except (RuntimeError, TypeError):   # already gone — fine
                        pass
                self._options_wired = False

        dock.closed.connect(_on_tool_closed)
        return dock

    def _wire_tool(self, key: str, widget) -> None:
        """Per-tool signal wiring that used to live inline in _build_central, run once when a
        tool dock is first created (the open-or-focus branch of open_tool returns before here, so
        a re-opened tool is never re-wired)."""
        if key == "data":
            widget.test_symbol_requested.connect(self._on_test_symbol)
            widget.test_dataset_requested.connect(self._on_test_dataset)
        elif key == "news":
            widget.itemsUpdated.connect(
                lambda w=widget: self._headlines_tile.set_items(w._items))
            if hasattr(widget, "set_symbol"):
                widget.set_symbol(self._symbol)
            # Seed the headlines tile from whatever the feed already merged, then arm the poller
            # (skipped under the headless kill-switch, mirroring _on_headlines_toggled).
            self._headlines_tile.set_items(widget._items)
            if not os.environ.get("VIKE_DISABLE_LIVE"):
                widget.start_feed(self._symbol)
        elif key == "options":
            self._wire_options(widget)   # binds the OptionsService to this tab + lazy-starts it

    def _open_in_new_chart(self, symbol: str) -> None:
        """Watchlist context menu → open ``symbol`` as a fresh chart document (current TF)."""
        self._new_chart_document(symbol)

    def _on_document_closed(self, doc) -> None:
        """A chart-document tab was closed (ADS DeleteOnClose): unregister + drop our ref."""
        self._live_hub.unregister(doc)
        self._link_bus.remove_member(doc)
        if doc in self._doc_widgets:
            self._doc_widgets.remove(doc)
        # Drop the now-orphaned content widget deterministically (the dock is DeleteOnClose but
        # not DeleteContentOnClose, so the ChartDocument would otherwise linger until GC).
        doc.deleteLater()

    def _broadcast_watchlist_symbol(self, symbol: str) -> None:
        """A watchlist pick also drives any chart in the watchlist's link colour (symbol only —
        each linked chart keeps its own interval). The main Chart space is loaded separately
        by _load_symbol, so it always follows the watchlist regardless of colour."""
        self._link_bus.broadcast(self._watchlist_link, self.watchlist, symbol=symbol)

    def _set_watchlist_link(self, gid: int) -> None:
        self._watchlist_link = gid

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
        #
        # The spaces are CDockWidget tabs of an ADS (Qt-Advanced-Docking-System) center area,
        # behind a QTabWidget-compatible facade (SpaceDeck) so all existing tab wiring holds.
        # ADS also hosts the side panels (dockable/floatable/pinnable) and provides the
        # save/restoreState used for layout persistence (and Phase 4's named workspaces).
        self._backtester = container
        configure_dock_manager_defaults()  # static config — must precede CDockManager()
        self.dock_manager = QtAds.CDockManager(self)  # installs itself as the central widget
        # Unified title bar (stage 1): our factory renders every dock-area title bar — the
        # central spaces area carries the single-title MC chart header; panels keep MC chrome.
        # Per-manager install (not the global setFactory) so offscreen tests + any future
        # manager keep ADS defaults. Keep a python ref so the factory isn't GC'd.
        from .dockshell import VikeComponentsFactory
        self._dock_factory = VikeComponentsFactory()
        self.dock_manager.setComponentsFactory(self._dock_factory)
        self.dock_manager.setStyleSheet(dock_qss())
        self.tabs = SpaceDeck(self.dock_manager)
        # The factory recreates the chart-space header on relayout; each time it asks us to
        # re-cap its width to the panel edge (forced far-right ⧉ ─ □ ✕).
        self.tabs.set_fit_callback(self._fit_chart_header)
        # the header is recreated on relayout; each time, repopulate its ● symbol + ◆ interval
        # link dots (state lives on the MainWindow, the dot WIDGETS are recreated with the header)
        self.tabs.set_header_status_provider(self._populate_header_status)
        self.tabs.addTab(container, "Chart")
        # Studio is the 8th on-demand DOCK now (not an eager space): its StudioTab + lockstep chart
        # are built by _build_studio_widget the first time the Studio dock opens. Only the Chart
        # space is added here, so SpaceDeck holds exactly ONE space and the app opens on Chart.
        # Bots panel (Active Bots / Historic Runs / Launch Bot) intentionally NOT mounted in
        # Studio for now — pending a refactor. self.bots stays alive so self.strategy /
        # self.history (its sub-widgets) keep serving show_strategy()/update_runs() calls.
        #
        # Replay/data controls (slider/speed/btn_*) are built EAGERLY and held un-parented here:
        # the backtest/replay/live pipeline (load_bars, _render_frame, _on_tick, forward) drives
        # them whether or not Studio is open, so they must always exist. _build_studio_widget
        # re-parents them into the Studio chart block; closing the Studio dock rescues them back
        # out (see _rescue_studio_controls) before the DeleteOnClose dock tree is destroyed.
        self._studio_controls, self._studio_scrubber = self._build_controls()
        # Tools tab hidden per user request (see import note above).
        # self.tools = ToolsTab()
        # self.tabs.addTab(self.tools, "Tools")
        #
        # The 7 non-Studio tools (screener/journal/alerts/data/news/calendar/options) are NO
        # LONGER eager SpaceDeck spaces — they open on-demand as dock widgets via open_tool(key)
        # (empty-workspace re-arch). Their legacy attributes (self.screener/.datamanager/.news/…)
        # are set by open_tool ONLY while the tool dock is open, and cleared on close, so code that
        # reads them keeps working when the tool is live and stays guarded otherwise. With Studio
        # now an on-demand dock too, SpaceDeck holds exactly ONE space: Chart (index 0). Per-tool
        # signal wiring that used to live here moved to _wire_tool(); the OptionsService stays
        # app-level (eager).
        self._options_svc = OptionsService(parent=self)
        self._options_started = False
        # Eagerly nil the legacy tool attrs so any reader site fails the getattr(...) guard until
        # the matching tool is opened (open_tool sets them; the dock-close handler re-nils them).
        for _attr in _TOOL_ATTR.values():
            setattr(self, _attr, None)

        # ADS makes the LAST-added dock current, so snap back to the Chart space before any
        # currentChanged consumers are wired.
        self.tabs.setCurrentIndex(0)
        # The left icon rail is RETIRED as visible chrome — the VS-Code-style menu bar in the
        # title bar (View/Go menus) took over navigation + panel toggles. _build_icon_rail()
        # still runs because its buttons/groups remain the single source of truth for space
        # sync (_rail_group), panel-toggle state (_panel_btns: session save/restore, Ctrl
        # shortcuts, palette commands) — the widget is just never mounted or shown.
        self._rail_state = self._build_icon_rail()
        self.tabs.currentChanged.connect(self._on_space_changed)

        # --- top command/launcher bar (S2) + VS-Code-style menu bar (S3) — MC16-style row ----
        from .menus import build_menu_bar
        from .topbar import CommandBar

        self.topbar = CommandBar(self._commands)
        self.topbar.set_menu_bar(build_menu_bar(self))
        self.topbar.symbolSubmitted.connect(self._on_topbar_symbol)
        self.topbar.intervalSubmitted.connect(self._on_topbar_interval)
        self.topbar.commandSubmitted.connect(self._run_command_label)
        # window-type launchers (the MC16 top-right cluster): a new chart window, the Studio dock,
        # and the on-demand tools (open as docks). Studio + the 7 tools are no longer spaces, so
        # their launchers call open_tool(key) (open-or-focus the dock).
        self.topbar.add_launcher("chart", "New chart window (Ctrl+N)",
                                 lambda: self._open_in_new_chart(self._symbol))
        self.topbar.add_launcher("studio", "Studio window", lambda: self.open_tool("studio"))
        # Topbar shows the 5 most-used tool launchers (width-limited); journal + alerts are
        # reachable via the rail + Go/File menus + Ctrl+K palette.
        for icon_name, label, key in (("screener", "Screener", "screener"), ("data", "Data", "data"),
                                      ("news", "News", "news"), ("calendar", "Calendar", "calendar"),
                                      ("options", "Options", "options")):
            self.topbar.add_launcher(icon_name, f"{label} window",
                                     lambda k=key: self.open_tool(k))
        # S6: the command bar lives IN the window's title bar (MC16) — one merged caption row
        # with the brand, ≡, command box, launchers and (frameless mode) min/□/✕. On Windows the
        # native caption is removed and a Win32 filter keeps move/Snap/resize/dbl-click native;
        # VIKE_NATIVE_TITLEBAR=1 (and non-Windows) falls back to this same bar below the OS one.
        from .titlebar import TitleBar, install_frameless

        self.titlebar = TitleBar(self, self.topbar)
        self.setMenuWidget(self.titlebar)   # spans the full width above toolbars/docks
        self._frameless_filter = install_frameless(self, self.titlebar)

        # Full-width separator hairlines (under the title bar + above the status bar), painted
        # in device-pixel space by one overlay so they match exactly at any display scaling.
        # The top one also replaces the rail's old vertical border.
        self._rules = _RuleOverlay(self)
        self._rules.raise_()
        self._update_chart_header()   # initial chart-space header title (CHART · SYM · iv)

    def _build_studio_widget(self) -> "StudioTab":
        """Build the Studio tab + its lockstep chart block on demand (Studio is the 8th closable
        dock now). Sets self.studio + self.studio_price; returns the StudioTab to host in the dock.

        Mirrors the eager construction that used to live in _build_central: a StudioTab whose
        'Chart' results tab hosts the studio PriceChart + the (eagerly-built) replay controls +
        the scrubber. The replay controls are re-parented IN here and rescued OUT on close
        (_rescue_studio_controls) so the always-on backtest/replay pipeline never loses them."""
        self.studio_price = PriceChart()
        # Match the eager wiring the Chart chart got in __init__ (timeframe reload + pairs fetch).
        self.studio_price.intervalChosen.connect(self._on_interval_chosen)
        self.studio_price.pairsRequested.connect(lambda n: self._add_pairs(self.studio_price, n))
        self.studio = StudioTab()
        self._wire_studio_agent()
        # Replay/data controls: a vertical button strip docked to the chart's RIGHT (fitted to its
        # height), with the scrubber on a full-width row BELOW the chart. Both are built eagerly and
        # held on self; re-parent them into this freshly-built block.
        _controls, _scrubber = self._studio_controls, self._studio_scrubber
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
        return self.studio

    def _rescue_studio_controls(self) -> None:
        """Re-parent the eager replay controls + scrubber OUT of the Studio dock's widget tree
        before it is destroyed (DeleteOnClose), so self.slider/.speed/.btn_* survive the close and
        the always-on backtest/replay pipeline keeps working. Best-effort: a torn-down C++ child is
        simply skipped."""
        for w in (getattr(self, "_studio_controls", None), getattr(self, "_studio_scrubber", None)):
            if w is None:
                continue
            try:
                w.setParent(None)
            except RuntimeError:   # already torn down with the dock — nothing to rescue
                pass

    def _wire_options(self, tab) -> None:
        """Connect an Options tab <-> the app-level service, then lazy-start its fetch.

        Empty-workspace re-arch: Options is now an on-demand dock, not a space, so wiring runs
        once when the dock is created (from _wire_tool) against the freshly built ``tab`` rather
        than the old eager ``self.options``. The app-level OptionsService stays single-bound for
        Plan 1 (re-opening the dock rebinds it to the new tab); a later sub-project makes it
        per-tab. Fetching is started here (was the space lazy-start) and is network-free under the
        headless kill-switch (OptionsService.load_expiries/start_polling honor VIKE_DISABLE_LIVE).
        One expiry at a time: the strip picks it; the service fetches+polls just that expiry."""
        svc = self._options_svc
        # Re-open path: the previous tab was DeleteOnClose-destroyed, but the service-side closures
        # from its wiring (_on_expiries etc.) would otherwise stay connected to svc and fire into a
        # dead C++ object. Drop ALL prior svc signal connections before rebinding to the new tab.
        # (Tab-side connections die with the deleted tab, so only svc needs explicit teardown.)
        # Guarded so we only disconnect when there WAS a prior wiring — disconnect() on an
        # unconnected signal emits a noisy libpyside RuntimeWarning.
        if getattr(self, "_options_wired", False):
            for _sig in (svc.chainReady, svc.failed, svc.expiriesReady):
                try:
                    _sig.disconnect()
                except (RuntimeError, TypeError):   # already gone — fine
                    pass
        self._options_wired = True
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
            if not self._options_all_expiries:
                tab.no_data(tab.underlying.currentText())   # don't leave a stale chain on screen
                return
            tab.set_expiries(_filtered())   # the strip auto-selects the nearest -> _select fires

        svc.expiriesReady.connect(_on_expiries)

        def _load_underlying(sym: str) -> None:
            svc.stop_polling()
            self._options_expiry = None
            tab.begin_load(sym)             # clear grid + show "Loading…" so a slow/empty fetch
            svc.set_underlying(sym)         # can't leave the previous symbol's chain displayed
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
        # Options-as-dock: start the fetch right after wiring (the space-switch lazy-start sites
        # in _on_tab_changed / _float_space are now inert — they keyed on the retired space).
        self._options_started = False   # fresh tab: re-arm so _maybe_start_options fetches
        self._maybe_start_options()

    def _maybe_start_options(self) -> None:
        if getattr(self, "options", None) is None:   # options dock not open -> nothing to start
            return
        if not self._options_started:
            self._options_started = True
            self._load_options_underlying(self.options.underlying.currentText())
        elif self._options_expiry is not None:
            self._options_svc.start_polling()  # resume the selected expiry's poll on re-open

    # Navigation splits into two kinds after the empty-workspace re-arch:
    #  * SPACES — the eager SpaceDeck tabs (just Chart now). Selected via tabs.setCurrentIndex.
    #    The space_index MUST match the addTab() order in _build_central.
    #  * TOOLS — the 8 on-demand docks (studio/screener/…/options). Opened via open_tool(tool_key).
    # Keep both in sync with _build_central (spaces) and ToolRegistry (tool keys).
    _SPACE_ITEMS = [("▤", "Chart", 0)]                              # (glyph, name, space_index)
    _TOOL_ITEMS = [("✦", "Studio", "studio"),
                   ("⊞", "Screener", "screener"), ("☰", "Journal", "journal"),
                   ("◉", "Alerts", "alerts"), ("◈", "Data", "data"),
                   ("📰", "News", "news"), ("▦", "Calendar", "calendar"),
                   ("⊗", "Options", "options")]                      # (glyph, name, tool_key)

    # PANELS section of the rail: independent show/hide toggles (TradeLocker style).
    # (key, icon_name, tooltip, shortcut). "backtester" toggles the centre chart; the others
    # map to docks in _panel_dock_map.
    _PANELS = [
        ("backtester", "chart", "Chart", "Ctrl+G"),
        ("market", "market", "Market watch", "Ctrl+M"),
        ("trades", "trades", "Trades & Positions", "Ctrl+T"),
        # Dashboard info tiles (Phase 6): small dockable widgets — arrange + pin + save a named
        # workspace to compose a personal dashboard. All default CLOSED on a fresh run.
        ("movers", "market", "Top movers", "Ctrl+Shift+M"),
        ("pnl", "scale", "P&L snapshot", "Ctrl+Shift+P"),
        ("ecal", "calendar", "Today's calendar", "Ctrl+Shift+E"),
        ("headlines", "news", "News headlines", "Ctrl+Shift+N"),
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
        # SPACES (Chart, Studio): exclusive, checkable, keyed by space_index so _on_tab_changed's
        # self._rail_group.button(index) keeps the active-space highlight in sync with the tabs.
        for _glyph, name, space_index in self._SPACE_ITEMS:
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(name.lower(), theme.TEXT3, theme.ACCENT, theme.TEXT2))
            b.setIconSize(QtCore.QSize(28, 28))
            b.setToolTip(self._chip_tip(name))
            b.setCheckable(True)
            b.setFixedSize(46, 46)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            b.clicked.connect(lambda _c, idx=space_index: self.tabs.setCurrentIndex(idx))
            self._rail_group.addButton(b, space_index)
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        # TOOLS (screener/…/options): each opens an on-demand dock — NOT part of the exclusive
        # space group (opening a tool dock doesn't change the current SPACE), so just action buttons.
        for _glyph, name, tool_key in self._TOOL_ITEMS:
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(name.lower(), theme.TEXT3, theme.ACCENT, theme.TEXT2))
            b.setIconSize(QtCore.QSize(28, 28))
            b.setToolTip(self._chip_tip(name))
            b.setFixedSize(46, 46)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            b.clicked.connect(lambda _c, k=tool_key: self.open_tool(k))
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)

        # "+ New chart" — opens the current symbol as a fresh tear-out chart document (not a
        # space, so it's an action button, not part of the exclusive rail group). Ctrl+N too.
        new_chart = QtWidgets.QToolButton()
        new_chart.setText("＋")
        new_chart.setToolTip(self._chip_tip("New chart", "Ctrl+N"))
        new_chart.setFixedSize(46, 46)
        new_chart.setCursor(QtCore.Qt.PointingHandCursor)
        new_chart.setStyleSheet(btn_qss)
        new_chart.clicked.connect(lambda: self._open_in_new_chart(self._symbol))
        col.addWidget(new_chart, 0, QtCore.Qt.AlignHCenter)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+N"), self,
                        activated=lambda: self._open_in_new_chart(self._symbol))
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+K"), self,
                        activated=self._open_command_palette)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+C"), self,
                        activated=self._copy_active_document)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+V"), self,
                        activated=self._paste_document)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F4"), self,
                        activated=self._close_active_window)

        # "Layout" — named workspaces menu (open / save / delete), rebuilt on each open so the
        # saved list is always current. Sits with the action buttons, not the exclusive rail.
        self._workspaces_btn = QtWidgets.QToolButton()
        self._workspaces_btn.setText("▤▥")
        self._workspaces_btn.setToolTip(self._chip_tip("Workspaces / Layout"))
        self._workspaces_btn.setFixedSize(46, 46)
        self._workspaces_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._workspaces_btn.setStyleSheet(btn_qss + "QToolButton::menu-indicator{image:none;}")
        self._workspaces_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        ws_menu = QtWidgets.QMenu(self._workspaces_btn)
        ws_menu.aboutToShow.connect(lambda m=ws_menu: self._populate_workspaces_menu(m))
        self._workspaces_btn.setMenu(ws_menu)
        col.addWidget(self._workspaces_btn, 0, QtCore.Qt.AlignHCenter)
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
        """Refresh the chart-space header on a space switch.

        (News is now an on-demand dock, not a space, so the old news lazy-start below is inert —
        a space widget never equals the live News tab; kept getattr-guarded, removed by a later
        sub-project. News arms its feed in _wire_tool when its dock opens.)"""
        self._update_chart_header()
        if index < 0:        # a chart document (not a space) became current — nothing to do
            return
        news = getattr(self, "news", None)
        if news is not None and self.tabs.widget(index) is news:
            news.start_feed(self._symbol)

    def _update_chart_header(self) -> None:
        """Drive the chart-space header title (the single MC-style line). For chart-bearing
        spaces it reads SPACE · SYMBOL · INTERVAL; other spaces show just their name. The live
        price (· 62,403 ▲0.18%) is appended by _header_price_html — the overcome-MC win."""
        if not hasattr(self, "tabs"):
            return
        idx = self.tabs.currentIndex()
        if idx < 0:                      # a chart document is current — leave the header as-is
            return
        name = (self._SPACE_ITEMS[idx][1] if idx < len(self._SPACE_ITEMS)
                else self.tabs.tabText(idx))
        icon_name = name.lower()
        if name in ("Chart", "Studio"):
            base = f"{name} · {self._symbol} · {self._interval}"
            html = self._header_price_html(base)
            if html is not None:
                self.tabs.set_header_title_rich(html)
            else:
                self.tabs.set_header_title(base)
        else:
            self.tabs.set_header_title(name)
        try:
            self.tabs.set_header_icon(
                icons.rail_icon(icon_name, theme.ACCENT, theme.ACCENT, theme.ACCENT)
                .pixmap(16, 16))
        except Exception:  # noqa: BLE001 - icon is cosmetic; never block the header on it
            pass
        self._fit_chart_header()

    def _header_price_html(self, base: str) -> "str | None":
        """The overcome-MC ticker: append the live last price + change% to the chart-space
        header (MC's title is static text). Reuses self._bars — no new data path, main-thread.
        Change is last close vs the prior bar's close, coloured green/red (theme UP/DOWN)."""
        bars = getattr(self, "_bars", None)
        if not bars:
            return None
        try:
            last = bars[-1].close
            prev = bars[-2].close if len(bars) >= 2 else bars[-1].open
        except (AttributeError, IndexError):
            return None
        chg = last - prev
        pct = (chg / prev * 100.0) if prev else 0.0
        col = theme.UP if chg >= 0 else theme.DOWN
        arrow = "▲" if chg >= 0 else "▼"
        return (f"<span style='color:{theme.TEXT};'>{base} · {last:,.2f} </span>"
                f"<span style='color:{col};'>{arrow}{abs(pct):.2f}%</span>")

    # --- central-chart symbol link (the header's ● / ◆ dots) --------------------------------

    def _populate_header_status(self, bar) -> None:
        """Add the chart-space header's ● symbol + ◆ interval link dots (called by the factory
        each time the header is (re)created — state lives on the MainWindow, dots are fresh)."""
        from .panels import LinkDot

        sym = LinkDot(self.link_group, label="Symbol")
        sym.groupChanged.connect(self._set_central_link_group)
        bar.add_status(sym)
        ivl = LinkDot(-1 if self.interval_link_group is None else self.interval_link_group,
                      label="Interval", glyph=("◇", "◆"), follow=True)
        ivl.groupChanged.connect(self._set_central_interval_link_group)
        bar.add_status(ivl)
        from .unifiedbar import FeedBadge

        self._header_feed = FeedBadge()
        bar.add_status(self._header_feed)
        self._render_header_feed()

    def _render_header_feed(self) -> None:
        """Paint the header's feed badge from the current feed state (compact: '● LIVE')."""
        badge = getattr(self, "_header_feed", None)
        if badge is None:
            return
        color, prefix = _FEED_STATES.get(self._feed_state, _FEED_STATES["idle"])
        try:
            badge.set_state(color, prefix.replace(" · ", "").strip() or "●")
        except RuntimeError:        # header was recreated — the old badge is gone
            self._header_feed = None

    def _set_central_link_group(self, gid: int) -> None:
        self.link_group = gid
        self._broadcast_central_link()

    def _set_central_interval_link_group(self, gid: int) -> None:
        self.interval_link_group = None if gid < 0 else gid
        self._broadcast_central_link()

    def _broadcast_central_link(self) -> None:
        """Push the central chart's (symbol, interval) to linked members. The bus re-entrancy
        guard makes this a no-op while we're applying a received broadcast (no ping-pong)."""
        bus = getattr(self, "_link_bus", None)
        if bus is not None:
            bus.broadcast(self.link_group, self, self._symbol, self._interval,
                          interval_group=self.interval_link_group)

    def apply_link(self, symbol: "str | None", interval: "str | None") -> None:
        """Bus receiver: a linked member changed — load it into the central chart. _load_symbol's
        own broadcast is suppressed by the bus guard, so this can't echo back."""
        self._load_symbol(symbol or self._symbol, interval or self._interval)

    def _fit_chart_header(self) -> None:
        """Cap the chart-space header to the VISIBLE chart width so its ⧉ ─ □ ✕ sit at the
        chart's right edge. The central dock area extends BEHIND the side panels (ADS), so the
        title bar's own width is useless here — instead measure where the right-hand panels
        actually start (their global left edge) and cap to that. Re-run on resize / panel
        toggle / space change / header recreation."""
        h = self.tabs.header_widget()
        if h is None:
            return
        try:
            hl = h.mapToGlobal(QtCore.QPoint(0, 0)).x()
            limit = self.mapToGlobal(QtCore.QPoint(self.width(), 0)).x()
            for dock in getattr(self, "_panel_dock_map", {}).values():
                try:
                    if dock.isClosed() or dock.isFloating():
                        continue
                    area = dock.dockAreaWidget()
                    if area is None or not area.isVisible():
                        continue
                    dl = area.mapToGlobal(QtCore.QPoint(0, 0)).x()
                    if dl > hl + 80:        # a panel docked to the RIGHT of the header
                        limit = min(limit, dl)
                except RuntimeError:        # dock torn down mid-relayout — skip
                    continue
            w = limit - hl
            if w > 120:
                h.setMaximumWidth(w)
        except RuntimeError:
            pass

    def _float_space(self, index: int) -> None:
        """Launcher click: open the space as a floating native window over the workspace
        (the chart-window experience for every space). Lazy services that normally start on
        a space *switch* must start here too — floating doesn't change the center tab.

        Empty-workspace re-arch: only Chart/Studio are spaces now, so the only caller passes
        Studio (index 1); the news/options branches below are inert (the floated widget never
        equals the live News/Options tab) and stay getattr-guarded — removed by a later cleanup."""
        self.tabs.float_space(index)
        widget = self.tabs.widget(index)
        if widget is getattr(self, "news", None):
            self.news.start_feed(self._symbol)
        elif widget is getattr(self, "options", None):
            self._maybe_start_options()
        # Stop the lazy service when the floating window is CLOSED — floating never changes the
        # center tab, so _on_space_changed's stop branch never fires for it (the Options 15s
        # poller would otherwise leak until the next center-space switch). Wire each dock once.
        if not hasattr(self, "_floated_close_wired"):
            self._floated_close_wired: set[int] = set()
        if index not in self._floated_close_wired:
            self._floated_close_wired.add(index)
            self.tabs.dock(index).closed.connect(
                lambda i=index: self._on_floated_space_closed(i))

    def _on_floated_space_closed(self, index: int) -> None:
        """A space's floating window was closed — tear down its lazy service (start/stop
        symmetry the center-tab navigation already provides)."""
        widget = self.tabs.widget(index)
        if widget is getattr(self, "options", None) and getattr(self, "_options_started", False):
            self._options_svc.stop_polling()

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
        # no pill/border per the user — just the dot + label, flush on the bottom bar.
        # Starts DIM (idle): green is earned by _update_feed_health once a feed is armed —
        # a default-green dot is a "connected" claim the app hasn't verified yet.
        self._feed_badge.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;background:transparent;"
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
        self._feed_state = state            # mirror onto the chart-space header badge
        self._render_header_feed()
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
        # No poller armed (VIKE_DISABLE_LIVE, or a pure cache load before/without arming) →
        # the watchdog must not claim LIVE off mere data freshness. _arm_live_updates starts
        # the timer BEFORE calling here, so a genuinely armed feed still classifies below.
        if not self._live_timer.isActive():
            self._set_feed_health("cached" if self._bars else "idle")
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
        # Test kill-switch: the offscreen suite (tests/conftest.py sets this) must do NO real
        # network I/O — the live updater spawns _LiveFetchWorker threads that hit Binance/Yahoo,
        # and a leaked one bleeds real fetches + renders into later, unrelated tests, stalling
        # the headless run (the data-layer-not-thread-safe / no-network-in-headless rule). The
        # live-edge merge logic itself is covered by unit tests (test_live_update.py).
        if os.environ.get("VIKE_DISABLE_LIVE"):
            return
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
        # Recorded-run / hand-loaded views still show data — say CACHED, not a bare dot.
        self._set_feed_health("cached" if self._bars else "idle")

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
            for ch in self._pipeline_charts():  # Chart space stays clean (no auto overlays)
                ch.apply_live(merged, overlays if ch is self.studio_price else None, repaint=False)
            self._update_chart_header()   # live ticker: header last price + change% (overcome MC)
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
    def _build_docks(self):
        # TradeLocker-style information architecture:
        #   Chart (centre) · Market watch (right) · Trades & Positions (full-width bottom).
        # Both are ADS panel docks now — UNLOCKED: drag to another edge, float to a separate
        # window (multi-monitor), or pin to the edge (auto-hide collapsed tab). The old locked
        # QDockWidget layout (fixed 300px, NoDockWidgetFeatures) is gone with the ADS shell.
        self.watchlist.setMinimumWidth(240)  # was setFixedWidth(300) when the layout was locked
        # Wrap the watchlist with a thin header carrying its symbol-link colour dot: a pick here
        # also drives every chart in the same colour (see _broadcast_watchlist_symbol).
        market_body = QtWidgets.QWidget()
        mb = QtWidgets.QVBoxLayout(market_body)
        mb.setContentsMargins(0, 0, 0, 0)
        mb.setSpacing(0)
        wl_header = QtWidgets.QHBoxLayout()
        wl_header.setContentsMargins(10, 4, 8, 4)
        link_lbl = QtWidgets.QLabel("Link")
        link_lbl.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;border:none;")
        wl_header.addWidget(link_lbl)
        wl_header.addStretch(1)
        self._watchlist_dot = LinkDot(self._watchlist_link)
        self._watchlist_dot.groupChanged.connect(self._set_watchlist_link)
        wl_header.addWidget(self._watchlist_dot)
        mb.addLayout(wl_header)
        mb.addWidget(self.watchlist)
        _ico = lambda n: icons.rail_icon(n, theme.TEXT2, theme.ACCENT, theme.TEXT)
        market = make_panel_dock(self.dock_manager, "MARKET WATCH", market_body,
                                 QtAds.RightDockWidgetArea, icon=_ico("market"))
        trades = make_panel_dock(self.dock_manager, "TRADES & POSITIONS",
                                 self._build_trades_panel(), QtAds.BottomDockWidgetArea,
                                 icon=_ico("trades"))

        # Dashboard info tiles (Phase 6): four small render-only docks fed from existing
        # main-thread state (watchlist quotes / current result / calendar cache / news feed).
        from .dashtiles import CalendarTile, MoversTile, NewsTile, PnLTile

        self._movers_tile = MoversTile()
        self._pnl_tile = PnLTile()
        self._ecal_tile = CalendarTile()
        self._headlines_tile = NewsTile()
        movers = make_panel_dock(self.dock_manager, "TOP MOVERS", self._movers_tile,
                                 QtAds.RightDockWidgetArea, icon=_ico("market"))
        pnl = make_panel_dock(self.dock_manager, "P&L", self._pnl_tile,
                              QtAds.RightDockWidgetArea, icon=_ico("scale"))
        ecal = make_panel_dock(self.dock_manager, "TODAY'S CALENDAR", self._ecal_tile,
                               QtAds.RightDockWidgetArea, icon=_ico("calendar"))
        headlines = make_panel_dock(self.dock_manager, "NEWS HEADLINES", self._headlines_tile,
                                    QtAds.RightDockWidgetArea, icon=_ico("news"))
        # News tile mirrors the News tool's merged feed. The News tool is now an on-demand dock,
        # so its itemsUpdated->tile connection is made in _wire_tool when the dock opens (not here
        # — self.news is None until then). Opening the headlines tile lazily starts the feed.
        headlines.viewToggled.connect(self._on_headlines_toggled)
        # Calendar tile reads the local week cache only (never network) — refill on open.
        ecal.viewToggled.connect(lambda on: on and self._refresh_calendar_tile())
        self._refresh_calendar_tile()

        # rail PANELS toggle targets (key must match _PANELS)
        self._market_dock = market
        self._trades_dock = trades
        self._panel_dock_map = {"market": market, "trades": trades, "movers": movers,
                                "pnl": pnl, "ecal": ecal, "headlines": headlines}
        self._docks = [market, trades, movers, pnl, ecal, headlines]
        # Programmatic open/close (space switches, rail toggles) is guarded so the user-driven
        # viewToggled (title-bar X / drag-close) is the only thing that updates the rail state.
        self._syncing_docks = False
        for key, dock in self._panel_dock_map.items():
            dock.viewToggled.connect(lambda on, k=key: self._on_dock_view_toggled(k, on))
        # Initial pane sizes for when the docks first open (both start closed — chart-first).
        try:
            self.dock_manager.setSplitterSizes(market.dockAreaWidget(), [1100, 300])
            self.dock_manager.setSplitterSizes(trades.dockAreaWidget(), [620, 190])
        except Exception:  # noqa: BLE001 - sizing is cosmetic; never block construction
            pass

    def _on_headlines_toggled(self, on: bool) -> None:
        """Opening the News-headlines tile seeds it from the already-merged feed and lazily
        starts the news poller (idempotent) — never under the headless kill-switch.

        News is now an on-demand dock: the tile mirrors it ONLY while the News dock is open
        (self.news is the live tab then, None otherwise). With the dock closed the tile keeps
        its last content rather than spawning an orphan poller with no tool to own it."""
        if not on:
            return
        news = getattr(self, "news", None)
        if news is None:
            return
        self._headlines_tile.set_items(news._items)
        if not os.environ.get("VIKE_DISABLE_LIVE"):
            news.start_feed(self._symbol)

    def _refresh_calendar_tile(self) -> None:
        """Fill the Today's-calendar tile from the LOCAL week cache only — the Calendar space
        owns fetching; the tile never touches the network."""
        from ..data.calendar.store import CalendarStore
        from .dashtiles_data import today_events

        try:
            now_ms = int(time.time() * 1000)
            events = CalendarStore().load_week(CalendarStore.iso_week_key(now_ms))
            self._ecal_tile.set_events(today_events(events or [], now_ms))
        except Exception:  # noqa: BLE001 - the tile is cosmetic; never block or modal
            self._ecal_tile.set_events([])

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
                f"color:{theme.TEXT};font-family:{theme.FONT_MONO};font-size:14px;font-weight:700;border:none;"
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
            self._pnl_tile.set_result([])
            return
        self._pnl_tile.set_result(res.equity_curve, res.final_equity)
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
            f"color:{color};font-family:{theme.FONT_MONO};font-size:14px;font-weight:700;border:none;"
        )
        self._acct["ret"].setText(f"{ret:+.2f}%")
        self._acct["ret"].setStyleSheet(
            f"color:{color};font-family:{theme.FONT_MONO};font-size:14px;font-weight:700;border:none;"
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

        # Chart-first on a FRESH run: Market watch + Trades start CLOSED (not active/opened).
        # Un-checking the rail toggle hides the dock via the wiring above; the toggle (or its
        # Ctrl shortcut) re-opens it. A saved session restores whatever the user last had open.
        saved = self._session.panels if self._session else {}
        for key, btn in self._panel_btns.items():
            # chart on; side panels + dashboard tiles closed on a fresh run
            fresh_default = key not in ("market", "trades", "movers", "pnl", "ecal",
                                        "headlines")
            btn.setChecked(bool(saved.get(key, fresh_default)))

    def _set_dock_open(self, dock, on: bool) -> None:
        """Programmatic open/close of an ADS panel dock. ``toggleView`` is the ADS way (plain
        setVisible desyncs its internal state); the guard keeps the resulting viewToggled
        signal from feeding back into the rail toggle bookkeeping. The guard SAVES/RESTORES its
        prior value rather than hard-clearing it, so a nested call (e.g. inside the restoreState
        guarded region) can't drop an outer guard on unwind."""
        prev = self._syncing_docks
        self._syncing_docks = True
        try:
            dock.toggleView(on)
            if on:
                self._ensure_dock_usable_width(dock)
        finally:
            self._syncing_docks = prev
        # a right-hand panel opening/closing changes the visible chart width -> re-pin header
        QtCore.QTimer.singleShot(0, self._fit_chart_header)

    def _on_dock_view_toggled(self, key: str, on: bool) -> None:
        """The user closed/opened a panel via its own chrome (title-bar X, pin, tab) —
        mirror it into the rail toggle + remembered intent so the two never fight."""
        if self._syncing_docks:
            return
        self._panel_visible[key] = on
        btn = self._panel_btns.get(key)
        if btn is not None and btn.isChecked() != on:
            btn.blockSignals(True)   # reflect state only — _toggle_panel already ran or must not
            btn.setChecked(on)
            btn.blockSignals(False)

    def _toggle_panel(self, key: str, on: bool) -> None:
        self._panel_visible[key] = on
        if self.tabs.currentWidget() is not self._backtester:
            return
        if key == "backtester":
            # Hide the centre (the spaces dock area); ADS expands the panel docks to fill.
            self.tabs.setVisible(on)
        else:
            self._set_dock_open(self._panel_dock_map[key], on)

    def _ensure_dock_usable_width(self, dock) -> None:
        """A dock restored from a session saved BEFORE it existed (e.g. the dashboard tiles
        on an older session) can land in a zero-width splitter slot — open but invisible.
        If its area is degenerate, hand it a usable share of its parent splitter."""
        area = dock.dockAreaWidget()
        if area is None or area.width() >= 40:
            return
        p = area.parentWidget()
        while p is not None and not isinstance(p, QtWidgets.QSplitter):
            p = p.parentWidget()
        if p is None or p.count() < 2:
            return
        want = 300
        sizes = p.sizes()
        total = sum(sizes) or max(self.width(), 800)
        idx = next((i for i in range(p.count())
                    if p.widget(i) is area or p.widget(i).isAncestorOf(area)), None)
        if idx is None:
            return
        remainder = max(total - want, p.count() - 1)
        others = [i for i in range(p.count()) if i != idx]
        share = remainder // max(len(others), 1)
        new_sizes = [share] * p.count()
        new_sizes[idx] = want
        p.setSizes(new_sizes)

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
        """Show the Chart docks only on the Chart tab (internally still keyed `backtester`); Studio/Tools are full-width.

        ``index`` is -1 when a chart DOCUMENT (not a space) is current — panels hide (a clean
        chart context, like a non-Chart space) and the title shows the document's symbol.
        """
        # Re-entrancy guard: toggling a panel dock here can drive ADS to re-emit the center
        # area's currentChanged synchronously and re-enter this slot — an unbounded close/reopen
        # loop (a verified stack overflow when a panel is dropped onto the spaces tab strip).
        if getattr(self, "_in_tab_change", False):
            return
        self._in_tab_change = True
        try:
            current = self.tabs.currentWidget()
            on_backtester = current is self._backtester
            on_document = isinstance(current, ChartDocument)
            # The centre must be visible to show any non-Backtester space; on the Backtester
            # space itself, honor the "backtester" hide toggle.
            self.tabs.setVisible(
                self._panel_visible.get("backtester", True) if on_backtester else True
            )
            # Panels show only on the Chart space, and there per each panel's remembered toggle.
            for key, dock in getattr(self, "_panel_dock_map", {}).items():
                self._set_dock_open(dock, on_backtester and self._panel_visible.get(key, True))
            btn = self._rail_group.button(index)  # keep the icon rail in sync with the tabs
            if btn is not None:
                btn.setChecked(True)
            # the OS title bar is the active-space indicator (tab strip + header chip are gone)
            if on_document:
                current.ensure_loaded()  # restored docs are cache-only until first focused
                self.setWindowTitle(f"vike-trader-app   {current.title()}")
            elif 0 <= index < len(self._SPACE_ITEMS):
                self.setWindowTitle(f"vike-trader-app   {self._SPACE_ITEMS[index][1]}")
            # Options is now an on-demand DOCK, not a space: it starts/stops in _wire_tool /
            # the dock-close handler, not on a space switch. This block is intentionally inert
            # (options is never the current space widget), kept getattr-guarded; a later
            # sub-project removes it.
            if getattr(self, "options", None) is not None and current is self.options:
                self._maybe_start_options()
        finally:
            self._in_tab_change = False

    def _wire_studio_agent(self) -> None:
        """Give the Studio a live Claude client iff an API key + the [ai] extra are present.

        No key -> the Studio's AI chat stays in the graceful 'No AI client configured' mode
        (and we avoid importing anthropic on every launch).
        """
        # The Studio AI panel now owns LLM-client construction: a provider toggle (Claude / Cerebras)
        # plus a BYO Cerebras key, persisted in QSettings (Claude still uses ANTHROPIC_API_KEY). It
        # builds the client on construction and only imports a provider SDK when a key is present, so
        # there is nothing to wire here at startup.
        return

    # --- Data tab → Studio ---
    def _on_test_symbol(self, symbol, bars) -> None:
        """Data tab → Studio: load one symbol's bars and open/focus the Studio dock."""
        if not bars:
            return
        self.open_tool("studio")   # open-or-focus: guarantees self.studio + raises the dock
        self.studio.set_bars(bars)

    def _on_test_dataset(self, dataset, bars_by_symbol) -> None:
        """Data tab → Studio: run the editor's strategy across the whole DataSet (portfolio backtest)."""
        self.open_tool("studio")   # open-or-focus: guarantees self.studio + raises the dock
        cls = self.studio.current_strategy_cls()
        if cls is None or not bars_by_symbol:
            return
        try:
            from ..core.portfolio_adapter import MultiSymbolStrategyRunner
            from ..tester import TesterConfig

            ranges = getattr(dataset, "ranges", None) or None  # dynamic/survivorship-free membership

            # --- benchmark symbol resolution (opt-in; falls back to equal-weight when unset) ---
            bench_sym = getattr(dataset, "benchmark", "")
            benchmark_bars = None
            if bench_sym:
                # prefer bars already loaded for this run (free); else try to load from cache
                benchmark_bars = bars_by_symbol.get(bench_sym)
                if not benchmark_bars:
                    try:
                        from ..data.parquet_source import read_series
                        dm = getattr(self, "datamanager", None)   # None when the Data dock is closed
                        if dm is not None:
                            loaded = read_series(dm._root, bench_sym, dataset.interval)
                            benchmark_bars = loaded if loaded else None
                        # else: leave benchmark_bars None -> equal-weight fallback below
                    except Exception:  # noqa: BLE001 - cache miss / import error -> graceful fallback
                        benchmark_bars = None

            report = MultiSymbolStrategyRunner(
                cls, bars_by_symbol, TesterConfig(), ranges=ranges,
                benchmark_bars=benchmark_bars,
                benchmark_label=bench_sym if bench_sym else "",
            ).report()
        except Exception as exc:  # noqa: BLE001 - missing module / resting orders unsupported in portfolio mode
            self.studio.results.show_error(f"Portfolio test failed: {exc}")
            return
        self.studio.show_portfolio_report(report, dataset.name,
                                          bars_by_symbol=bars_by_symbol, ranges=ranges)

    def _pipeline_charts(self):
        """Charts that the data/replay/live pipeline feeds: the Chart space always, plus the
        Studio chart only while the Studio dock is open (studio_price is None when it's closed)."""
        charts = [self.price]
        if self.studio_price is not None:
            charts.append(self.studio_price)
        return charts

    # --- data / strategy loading ---
    def load_bars(self, bars, strategy_factory=None, *, record=True):
        if strategy_factory is not None:
            self._strategy_factory = strategy_factory
        self.strategy.show_strategy(self._strategy_factory)
        self._bars = bars
        if self.studio is not None:
            self.studio.set_bars(bars)  # the Studio tab backtests the same data (when open)
        self._result = BacktestEngine(bars, self._strategy_factory()).run()
        self._replay = Replay(len(bars))
        # A freshly loaded chart shows the LIVE EDGE (latest bars), like TradingView — not bar 0.
        # Replay starts its cursor at index 0; without this seek, _render_frame() below would
        # re-frame the view back to the oldest bars (jamming all candles to the left) whenever the
        # slider's setValue doesn't fire a seek (same-length reload, interval switch, auto-load).
        self._replay.seek(self._replay.last_index)
        overlays = self._strategy_factory().chart_overlays([b.close for b in bars])
        for ch in self._pipeline_charts():
            # The Chart space is a CLEAN viewer — no auto strategy markers OR overlays (those belong
            # to the Studio/backtest chart). Trades + the SMA legend go to Studio only; indicators on
            # the Chart space come only from ƒx Indicators — matching a plain TradingView chart.
            ch.set_data(bars, self._result.trades if ch is self.studio_price else [])
            ch.set_overlays(overlays if ch is self.studio_price else {})
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
            self._movers_tile.merge_prices({symbol: quote})

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
            self._movers_tile.merge_prices(chunk)

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
        if getattr(self, "news", None) is not None:   # forward to the News tool only while open
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
        self._update_chart_header()
        self._broadcast_central_link()   # propagate to linked charts/watchlist (bus-guarded)

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
        for ch in self._pipeline_charts():
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
        for ch in self._pipeline_charts():
            ch.set_data(self._fwd_bars, res.trades)
            ch.set_overlays(overlays if ch is self.studio_price else {})  # Chart space stays clean
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
        self._fit_chart_header()   # chart width changed -> re-pin the header's ⧉ ─ □ ✕

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

    def _capture_state(self) -> SessionState:
        """Snapshot the live shell into a SessionState — the shared payload for both the
        launch-restore session file and named workspaces."""
        return SessionState(
            symbol=self._symbol,
            interval=self._interval,
            # -1 (a chart document was current) falls back to the Chart space on reopen.
            space=max(self.tabs.currentIndex(), 0),
            geometry_hex=bytes(self.saveGeometry().toHex()).decode("ascii"),
            dock_state_hex=bytes(self.dock_manager.saveState().toHex()).decode("ascii"),
            maximized=self.isMaximized(),
            panels=dict(self._panel_visible),
            chart_indicators=indicator_states(self.price),
            # Studio chart only exists while its dock is open; capture its indicators when present.
            studio_indicators=(indicator_states(self.studio_price)
                               if self.studio_price is not None else []),
            documents=[self._doc_state_with_geometry(d) for d in self._doc_widgets],
            open_tools=list(self._tool_docks.keys()),
            watchlist_link=self._watchlist_link,
            central_link=self.link_group,
            central_interval_link=(-1 if self.interval_link_group is None
                                   else self.interval_link_group),
        )

    def _doc_state_with_geometry(self, doc) -> dict:
        """A chart window's persisted state incl. its frame geometry (x, y, w, h)."""
        st = doc.state()
        frame = self._frame_of(doc)
        if frame is not None and not frame.is_detached():
            g = frame.geometry()
            st["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        return st

    def _save_session(self) -> None:
        """Snapshot the session to disk (no-op when persistence is disabled)."""
        if not self._session_path:
            return
        save_session(self._session_path, self._capture_state())

    # --- named workspaces (Phase 4) ---------------------------------------------------------
    def _apply_workspace_state(self, state: SessionState) -> None:
        """Replace the live shell with a workspace: swap the open documents, then restore its
        dock layout / panels / active space / watchlist link. Reuses the Phase 2 recreate-docs
        path; the document set is replaced wholesale (a workspace owns its windows)."""
        # Apply the panel intent FIRST: close_all_documents() can fire currentChanged ->
        # _on_tab_changed synchronously, and we want that to already reflect the incoming
        # workspace's panel visibility (not the outgoing one) to avoid an intermediate flicker.
        merged = dict(self._panel_visible)
        merged.update(state.panels or {})        # partial workspace keeps the unspecified panels
        self._panel_visible = merged
        for key, btn in self._panel_btns.items():
            if key in merged:
                btn.blockSignals(True)           # reflect intent without re-driving _toggle_panel
                btn.setChecked(bool(merged[key]))
                btn.blockSignals(False)

        self._close_all_chart_windows()         # each close unregisters hub/bus + drops refs

        # Close any open tool docks first — the incoming workspace fully defines which tools
        # are open (via its open_tools); leaving stale docks open collides with the layout blob.
        for _dock in list(self._tool_docks.values()):
            try:
                _dock.closeDockWidget()   # fires _on_tool_closed -> clears _tool_docks + alias
            except Exception:  # noqa: BLE001
                pass

        self._doc_seq = 0                        # fresh doc:N names that match this state's blob
        for st in (state.documents or []):
            self._new_chart_document(st.get("symbol", "BTCUSDT"), st.get("interval", "1h"),
                                     state=st, network=False, make_current=False)

        # Recreate the workspace's tool docks BEFORE restoreState so their tool:<key> objectNames
        # exist for the layout blob to position 1:1 (else a Screener/News dock saved into the
        # workspace would leave dangling references and not reappear). Unlike chart docs
        # (network=False), a restored News/Calendar dock starts its HTTP feed immediately on
        # open_tool — acceptable (HTTP, not the non-thread-safe data layer) and reworked by
        # Plan 2's shared feed hubs. The dock must exist now (no deferral) — restoreState needs it.
        for key in (state.open_tools or []):
            try:
                self.open_tool(key)
            except Exception:  # noqa: BLE001 - one bad/stale tool key must not break the load
                pass

        self._watchlist_link = state.watchlist_link if state.watchlist_link in LINK_COLOR else 0
        self._watchlist_dot.set_group(self._watchlist_link)

        if state.dock_state_hex:                 # a saved layout; built-ins use default positions
            self._syncing_docks = True
            try:
                self.dock_manager.restoreState(
                    QtCore.QByteArray.fromHex(state.dock_state_hex.encode("ascii")))
            except Exception:  # noqa: BLE001 - stale/garbled blob -> keep the rebuilt default
                pass
            finally:
                self._syncing_docks = False
        self.tabs.hide_space_tabs()   # restoreState re-shows space tabs; the rail switches spaces

        space = min(max(state.space, 0), self.tabs.count() - 1)
        self.tabs.setCurrentIndex(space)
        self._on_tab_changed(self.tabs.currentIndex())   # applies panel visibility per intent

    def _apply_workspace(self, name: str) -> bool:
        state = self._workspaces.load(name)
        if state is None:
            return False
        self._apply_workspace_state(state)
        self._workspaces.record_recent(name)   # File > Recent Workspaces (S4)
        return True

    def _save_workspace_as(self, name: str) -> bool:
        """Capture the live shell as a named workspace (overwrites an existing user one)."""
        if not name:
            return False
        return self._workspaces.save(name, self._capture_state())

    def _delete_workspace(self, name: str) -> bool:
        return self._workspaces.delete(name)

    def _populate_workspaces_menu(self, menu: QtWidgets.QMenu) -> None:
        """Fill the Layout menu fresh each open: open a saved/built-in workspace, or save/delete."""
        menu.clear()
        for name in self._workspaces.names():
            tag = "" if self._workspaces.is_user(name) else "  (built-in)"
            menu.addAction(name + tag, lambda n=name: self._apply_workspace(n))
        menu.addSeparator()
        menu.addAction("Save current as…", self._prompt_save_workspace)
        menu.addAction("AI: generate a layout…", self._prompt_ai_layout)
        user = [n for n in self._workspaces.names() if self._workspaces.is_user(n)]
        if user:
            sub = menu.addMenu("Delete")
            for name in user:
                sub.addAction(name, lambda n=name: self._delete_workspace(n))

    def _prompt_save_workspace(self) -> None:
        """Live-app only: ask for a name and save the current layout (no modal in tests)."""
        name, ok = QtWidgets.QInputDialog.getText(self, "Save workspace", "Workspace name:")
        if ok and name.strip():
            self._save_workspace_as(name.strip())

    # --- command palette (Ctrl+K, Phase 5) --------------------------------------------------
    def _commands(self) -> list:
        """The flat (label, callback) command list the Ctrl+K palette fuzzy-searches."""
        cmds: list = []
        for _glyph, name, space_index in self._SPACE_ITEMS:   # Chart -> switch space
            cmds.append((f"Go to {name}", lambda idx=space_index: self.tabs.setCurrentIndex(idx)))
        for _glyph, name, tool_key in self._TOOL_ITEMS:        # Studio + the 7 tools -> open the dock
            cmds.append((f"Open {name}", lambda k=tool_key: self.open_tool(k)))
        for name in self._workspaces.names():
            cmds.append((f"Open workspace: {name}", lambda n=name: self._apply_workspace(n)))
        cmds.append((f"New chart: {self._symbol}",
                     lambda: self._open_in_new_chart(self._symbol)))
        cmds.append(("Arrange charts: tile grid",
                     lambda: self._arrange_chart_windows("grid")))
        cmds.append(("Arrange charts: side by side",
                     lambda: self._arrange_chart_windows("columns")))
        cmds.append(("Arrange charts: stacked",
                     lambda: self._arrange_chart_windows("rows")))
        cmds.append(("Arrange charts: cascade",
                     lambda: self._arrange_chart_windows("cascade")))
        cmds.append(("Save workspace as…", self._prompt_save_workspace))
        cmds.append(("AI: generate a layout…", self._prompt_ai_layout))
        cmds.append(("Copy window", self._copy_active_document))
        cmds.append(("Paste window", self._paste_document))
        cmds.append(("Float current document", self._float_current_document))
        for key, _icon, tip, _sc in self._PANELS:
            cmds.append((f"Toggle panel: {tip}", lambda k=key: self._panel_btns[k].toggle()))
        return cmds

    def _run_command_label(self, label: str) -> None:
        """Execute a palette command by its label (the top bar's command path)."""
        for cmd_label, callback in self._commands():
            if cmd_label == label:
                callback()
                return

    def _open_command_palette(self) -> None:
        from .command_palette import CommandPalette

        pal = CommandPalette(self._commands(), self)
        center = self.geometry().center()
        pal.move(center.x() - 280, center.y() - 160)
        pal.exec()

    # --- top-bar routing + window verbs (S2/S4 of the shell-ux plan) -------------------------
    def _on_topbar_symbol(self, symbol: str) -> None:
        """Symbol typed in the command bar: route to the ACTIVE chart window if one is up,
        else the Chart space (switching there so the user SEES the result)."""
        if self._active_frame is not None and self._active_frame in self._chart_frames:
            self._active_frame.doc.load(symbol=symbol)
            return
        self.tabs.setCurrentIndex(0)
        self._load_symbol(symbol)

    def _on_topbar_interval(self, interval: str) -> None:
        if self._active_frame is not None and self._active_frame in self._chart_frames:
            self._active_frame.doc.load(interval=interval)
            return
        self._on_interval_chosen(interval)

    def _copy_active_document(self) -> None:
        """MC's Copy Window: serialize the active chart window (or the Chart space's view)
        to the clipboard as JSON; Paste recreates it — works across app instances too."""
        if self._active_frame is not None and self._active_frame in self._chart_frames:
            payload = self._doc_state_with_geometry(self._active_frame.doc)
        else:
            payload = {"symbol": self._symbol, "interval": self._interval,
                       "indicators": indicator_states(self.price)}
        QtWidgets.QApplication.clipboard().setText(
            json.dumps({"vike_window": payload}))
        self.statusBar().showMessage("Window copied — paste with Ctrl+Shift+V", 4000)

    def _paste_document(self) -> None:
        try:
            raw = json.loads(QtWidgets.QApplication.clipboard().text())
            state = raw["vike_window"]
        except Exception:  # noqa: BLE001 - clipboard isn't ours — ignore quietly
            self.statusBar().showMessage("Clipboard has no copied window.", 3000)
            return
        self._new_chart_document(state.get("symbol", self._symbol),
                                 state.get("interval", self._interval), state=state)

    def _float_current_document(self) -> None:
        """Detach the active chart window to its own OS window (MC's Detach), or re-attach."""
        if self._active_frame is not None and self._active_frame in self._chart_frames:
            self._active_frame.toggle_detach()
            return
        self.statusBar().showMessage("Focus a chart window to detach it.", 3000)

    def _close_active_window(self) -> None:
        """MC's Close Window (Ctrl+F4): close the active chart window."""
        if self._active_frame is not None and self._active_frame in self._chart_frames:
            self._active_frame.close_window()
            return
        self.statusBar().showMessage("Focus a chart window to close it.", 3000)

    def _activate_document(self, doc) -> None:
        frame = self._frame_of(doc)
        if frame is not None:
            frame.raise_()
            self._on_chart_window_activated(frame)

    def _export_chart_image(self) -> None:
        """Save the active chart (document or Chart space) as a PNG. Interactive path only."""
        chart = getattr(self.tabs.currentWidget(), "chart", None) or self.price
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export chart image", f"{self._symbol}.png", "PNG image (*.png)")
        if path:
            chart.grab().save(path)
            self.statusBar().showMessage(f"Saved {path}", 4000)

    def _show_shortcuts(self) -> None:
        rows = ["Ctrl+K — command palette", "Ctrl+N — new chart window",
                "/ — focus the command bar", "Ctrl+Shift+C / V — copy / paste window",
                "Ctrl+G / Ctrl+M / Ctrl+T — chart / market watch / trades panels",
                "Middle-click tab — close window", "Double-click tab — float window"]
        QtWidgets.QMessageBox.information(self, "Keyboard shortcuts", "\n".join(rows))

    def _show_about(self) -> None:
        QtWidgets.QMessageBox.about(
            self, "vike-trader",
            "vike-trader — crypto-first backtesting & forward-testing desktop.\n"
            "Charting · Studio (AI strategy lab) · Screener · Options · Calendars.")

    # --- agent-emitted workspaces (Phase 5) -------------------------------------------------
    def _apply_agent_spec(self, spec: dict) -> None:
        """Convert an agent ``create_workspace`` spec into a SessionState and apply it — same
        path as opening a saved workspace, so an AI layout and a hand-saved one are identical."""
        from .workspaces import workspace_from_agent_spec

        self._apply_workspace_state(workspace_from_agent_spec(spec))

    def _build_layout_client(self):
        """A Claude client for the layout agent, or None if no API key / the [ai] extra."""
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        try:
            from ..ai.llm import ClaudeClient
            return ClaudeClient()
        except Exception:  # noqa: BLE001 - extra missing / SDK import failure
            return None

    def _ai_generate_layout(self, prompt: str, client=None) -> None:
        """Run the layout agent off-thread and apply its spec when it returns. ``client`` is
        injectable for tests; in the app it's built from ANTHROPIC_API_KEY."""
        if not prompt.strip():
            return
        client = client or self._build_layout_client()
        if client is None:
            self.statusBar().showMessage(
                "AI layout needs ANTHROPIC_API_KEY (and the [ai] extra).", 5000)
            return
        self.statusBar().showMessage("Generating layout…", 3000)
        # Supersede any in-flight request: an API call can't be cancelled, but disconnecting its
        # done signal stops a slow earlier worker from clobbering this newer layout when it lands.
        for w in self._layout_workers:
            try:
                w.done.disconnect(self._on_ai_layout)
            except (RuntimeError, TypeError):
                pass
        worker = _WorkspaceAgentWorker(client, prompt, self)
        self._layout_workers.append(worker)   # tracked so closeEvent waits every in-flight thread
        worker.done.connect(self._on_ai_layout)
        worker.finished.connect(lambda w=worker: w in self._layout_workers
                                and self._layout_workers.remove(w))
        worker.start()

    def _on_ai_layout(self, spec) -> None:
        if spec:
            self._apply_agent_spec(spec)
        else:
            self.statusBar().showMessage("The layout agent returned nothing.", 4000)

    def _prompt_ai_layout(self) -> None:
        """Live-app only: ask for a description and generate a layout (no modal in tests)."""
        text, ok = QtWidgets.QInputDialog.getText(
            self, "AI layout", "Describe the layout you want:")
        if ok and text.strip():
            self._ai_generate_layout(text.strip())

    def closeEvent(self, event):  # noqa: N802 - Qt override
        self._save_session()  # snapshot before teardown so the next launch resumes here
        if getattr(self, "_link_bus", None) is not None:
            self._link_bus.remove_member(self)   # leave the bus: no apply_link after teardown
        self._stop_forward()  # never leave a feed thread running
        # Halt the live chart auto-updater AND wait out any in-flight fetch worker. Without
        # this a closed window keeps its _live_timer firing _live_tick -> _LiveFetchWorker
        # (real network) -> _on_live_fetched render; in the offscreen test process that leaked
        # work bleeds into later, unrelated tests (e.g. a bare NewsTab test pumping
        # processEvents), stalling the suite. The other long-lived timers/threads below were
        # already stopped here — _live_timer/_live_worker were simply missed.
        if getattr(self, "_live_hub", None) is not None:
            self._live_hub.shutdown()  # stop the chart-document live round-robin + its worker
        self._stop_live_updates()
        if getattr(self, "_live_worker", None) is not None:
            self._live_worker.wait(2000)
            self._live_worker = None
        for w in list(getattr(self, "_layout_workers", [])):
            w.wait(5000)  # wait out any in-flight layout-agent API call (no QThread-destroyed)
        self._layout_workers = []
        if getattr(self, "news", None) is not None:
            self.news.stop_feed()  # halt the news poller thread (only if the News dock is open)
        if self.studio is not None:
            self.studio.shutdown()  # wait out any in-flight AI worker (only if the Studio dock is open)
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
    notice, and the font-fallback point-size notice it triggers) while routing every other
    Qt message through the ``vike.qt`` logger (so it lands in the rotating file log too).

    'QFont::setPointSize: Point size <= 0 (-1)' is a downstream symptom of the same missing-font
    fallback ('Cannot find font directory'): when a requested family isn't installed, Qt resolves
    a substitute whose point size is unset (-1) and warns while keeping the valid size. No
    functional impact — text still renders via the fallback family."""
    import logging

    from ..crash import report_qt as _report_qt

    _benign = ("Cannot find font directory", "propagateSizeHints",
               "QFont::setPointSize: Point size", "QFont::setPixelSize: Pixel size")
    qt_log = logging.getLogger("vike.qt")
    # QtMsgType: Debug=0, Warning=1, Critical=2, Fatal=3, Info=4
    _levels = {0: logging.DEBUG, 1: logging.WARNING, 2: logging.ERROR,
               3: logging.CRITICAL, 4: logging.INFO}

    def handler(mode, ctx, msg):  # noqa: ANN001
        if any(s in msg for s in _benign):
            return
        qt_log.log(_levels.get(int(mode), logging.WARNING), "%s", msg)
        if int(mode) == 3:  # QtFatalMsg — the app is aborting; spool it for next-launch report
            _report_qt(int(mode), msg)

    QtCore.qInstallMessageHandler(handler)


def main():
    import logging
    import sys

    try:  # honor .env so API keys / options-backend config are picked up (python-dotenv is a core dep)
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 - missing .env / dotenv must never block launch
        pass

    from ..logging_setup import configure_logging

    log_path = configure_logging()  # rotating file (logs/) + console; Qt filter feeds it below
    logging.getLogger("vike.app").info("starting vike-trader-app (log file: %s)", log_path)

    from ..crash import install as install_crash  # crash capture + drain last run's spool (opt-in upload)

    try:
        from importlib.metadata import version as _pkg_version

        _ver = _pkg_version("vike-trader-app")
    except Exception:  # noqa: BLE001 - metadata may be absent in a source checkout
        _ver = None
    install_crash(app_version=_ver)
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
    # Open MAXIMIZED so the window always fits the screen. The fixed 1440x900 + plain show()
    # overflowed on 1366x768-class laptops (the 900px height exceeded the usable screen, so
    # _center_on_screen shrank it). _center_on_screen still runs in __init__, so un-maximizing
    # restores a screen-fitted, centered window. A restored session that was left UN-maximized
    # opens at its saved geometry instead.
    if win._restored_geometry and win._session is not None and not win._session.maximized:
        win.show()
    else:
        win.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
