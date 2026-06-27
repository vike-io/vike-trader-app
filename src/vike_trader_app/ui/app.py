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
from ..exec.oms import OmsHub
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

from . import icons, theme, toolreg
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
_FUNDING_POLL_MS = 5 * 60 * 1000  # 5e: REST funding poll cadence (~5 min); funding settles ~8h
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

    def set_lines(self, bottom_y: int) -> None:
        if bottom_y != self._bottom_y:
            self._bottom_y = bottom_y
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
        painter.end()


class MainWindow(QtWidgets.QMainWindow):
    """Main application window."""

    def __init__(self, session_path: str | None = _SESSION_PATH):
        super().__init__()
        self.setWindowTitle("vike-trader-app")  # updated to the focused chart's title on activation
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

        # Teardown gate. Set True the instant shutdown() begins (closeEvent OR the test harness).
        # Every dock-touching slot that a Qt signal / deferred callback can fire DURING teardown
        # checks it and no-ops, so the ordered shutdown can close the ADS graph without a stray
        # toggleView / relayout re-entering a half-freed dock (the CDockWidget-already-deleted +
        # heap-corruption race). Set FIRST in __init__ so every later-connected slot sees it.
        self._closing = False

        self._bars = []
        self._result = None
        self._replay = Replay(0)
        self._strategy_factory = default_strategy_factory()
        self._symbol = self._session.symbol if self._session else "BTCUSDT"
        # 6e: a Deribit options-chain pick sets this; the arm path prefers it over self._symbol so a
        # picked contract arms WITHOUT typing it as the chart symbol. None for spot/perp (byte-identical).
        self._exec_symbol_override: str | None = None
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
        # Chart-unify keystone: there is NO docked central chart anymore. Every chart is a
        # floating ChartWindowFrame peer (so Window>Arrange tiles them all uniformly, no overlap).
        # `self.price` is no longer an owned PriceChart — it TRACKS the focused frame's chart
        # (set in _set_active_frame), or None when no chart is focused. Every read is None-safe
        # (made so in #166); writes happen only via _set_active_frame.
        self.price = None                 # -> focused chart frame's PriceChart, or None
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
        self._exec_session = None   # LiveExecutionSession when live exec is gated ON (Phase 3b)
        self._doc_seq = 0                  # monotonic id for stable dock objectNames (doc:N)
        self._doc_widgets: list[ChartDocument] = []
        self._chart_frames: list = []      # MC-style floating ChartWindowFrames (S7)
        self._active_frame = None
        # Floating windows do NOT auto-arrange: adding / deleting / minimizing a window or resizing
        # the workspace leaves every other window exactly where the user put it. Windows tile ONLY
        # when the user explicitly picks Window>Arrange (_arrange_chart_windows); `_last_arrange_mode`
        # just records the last mode chosen there. (A MAXIMIZED window still re-fills on resize via
        # _reflow_frames/host_resized — that's maximize behaviour, not arrange.)
        self._last_arrange_mode = "grid"
        # empty-workspace re-arch: open non-chart tools (screener/journal/… ) lazily as docks,
        # keyed by tool key for singleton open-or-focus (Plan 1). See ui/toolreg.py.
        self._tool_docks: dict[str, "QtAds.CDockWidget"] = {}
        # Stage A2: a tool torn out to a clean chartwin-style window lives here (keyed by tool key)
        # instead of in _tool_docks — the SAME live widget, just re-homed. _tool_detaching marks a
        # dock close that is really a detach (the widget lives on), so its close handler skips the
        # teardown (stop_feed / alias-nil / studio-shutdown) that a real close runs.
        self._tool_frames: dict[str, "ToolWindowFrame"] = {}
        self._tool_detaching: set[str] = set()
        # Side panels (Market watch / Trades) unify to the same ⧉ ─ □ ✕ as tools/charts: ⧉ floats
        # the panel into a window (lives here, keyed by the dock objectName), ─ auto-hides to the
        # edge, □ maximizes it in the workspace, ✕ closes. _panel_detaching marks a dock close that
        # is really a float (skip the rail-toggle off-sync); _panel_max_* hold the maximize restore.
        self._panel_frames: dict[str, "ToolWindowFrame"] = {}
        self._panel_detaching: set[str] = set()
        # Maximize state — ONE machine for the chart header AND every side panel (see _maximize_dock).
        # _maximized is the dock filling the workspace (chart space or a panel); _max_hidden is what
        # was hidden to make room. _panel_maxed is the compat flag the dock title-bar glyph code
        # (dockshell) reads to mark a maximized panel area.
        self._maximized = None
        self._max_hidden: list = []
        self._panel_maxed: "str | None" = None
        # A chart window docked into the workspace ("Dock into workspace") lives here as an ADS
        # dock (objectName chart:<n>); it tears back out to a clean window via the dock's ⧉.
        # _chart_detaching marks a dock close that is really a tear-out (the doc lives on).
        self._chart_docks: dict[str, "QtAds.CDockWidget"] = {}
        self._chart_detaching: set[str] = set()
        self._chart_seq = 0

        self._layout_workers: list = []   # in-flight AI-layout agent threads (Phase 5)

        # symbol link groups (Phase 3): charts + the watchlist sharing a colour move together.
        self._link_bus = SymbolLinkBus()
        self._watchlist_link = self._session.watchlist_link if self._session else 0
        if self._watchlist_link not in LINK_COLOR:   # hand-edited / stale session -> unlinked
            self._watchlist_link = 0

        # named workspaces (Phase 4): persisted next to the session file; in-memory when the
        # session is disabled (offscreen tests) so a save never touches real storage.
        self._workspaces = WorkspaceStore(
            str(Path(self._session_path).with_name("workspaces.json"))
            if self._session_path else None
        )
        # No central chart to wire: each ChartDocument wires its own intervalChosen (chartdoc.py)
        # and _make_chart_frame wires its pairsRequested. The Studio chart's signals are wired in
        # _build_studio_widget when the Studio dock is built.

        # Header crumb removed — it duplicated the chart's OHLC legend + the status bar.
        # Keep the label as a hidden status sink so existing setText() calls (and tests) work.
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
        # 5e: poll REST funding (OKX bills / Bybit transaction-log) on the MAIN thread — the
        # poller.publish() must run on the single-writer thread (same as LiveExecutionSession._on_report).
        self._funding_pollers: list[object] = []
        self._funding_timer = QtCore.QTimer(self)
        self._funding_timer.timeout.connect(self._funding_tick)

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
            self._reclaim_floating_docks()   # un-float any dock a stale blob restored as a native float
            # ADS materializes a restored CFloatingDockContainer on a DEFERRED tick — AFTER the
            # synchronous call above, so floatingWidgets() was still empty and a stale float (e.g. a
            # session-saved floating Market Watch) survived launch with native chrome (app icon +
            # size grip), the ONE title bar that isn't our unified bar. Re-run on the next event-loop
            # turn to catch it (tied to self so a closed window cancels it, not crash on a dead obj).
            QtCore.QTimer.singleShot(0, self, self._reclaim_floating_docks)
        # restoreState rebuilds tab widgets and re-shows the space tabs — re-hide them (the
        # rail is the space switcher; the center strip carries only chart documents).
        self.tabs.hide_space_tabs()

        # Stage A3: reopen the last session's torn-out TOOL windows. They're chartwin frames (not
        # ADS docks), so they live OUTSIDE the dock_state blob — recreate them AFTER restoreState
        # at their saved attached geometry (or cascaded when none was saved / it was an OS window).
        if self._session and getattr(self._session, "tool_windows", None):
            for spec in self._session.tool_windows:
                if not isinstance(spec, dict):
                    continue
                try:
                    self._open_tool_window(spec.get("key"), spec.get("geometry"))
                except Exception:  # noqa: BLE001 - one bad/stale tool key must not break launch
                    pass

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

        # NO auto-created chart. A returning user's charts are recreated from session.documents
        # above; a fresh start / explicitly-emptied workspace stays EMPTY (the user's "if no chart
        # open don't open one" / "if workspace saved empty don't autocreate chart"). Charts open
        # on demand via the topbar "New chart" launcher / Ctrl+N.

        # Top up whatever chart frames were restored (cache-first, main thread per the data-layer
        # constraint). No central chart to seed — _startup_load now just re-applies per-doc state.
        QtCore.QTimer.singleShot(200, self._startup_load)

    def _startup_load(self) -> None:
        """Re-apply the Studio chart's saved indicators when its dock is open.

        Post-keystone there is NO central chart to seed: each restored chart document loads +
        restores its OWN symbol/indicators in _new_chart_document, and a fresh start stays empty.
        We must NOT drive `_load_symbol(self._symbol)` here — with a chart focused that would
        OVERWRITE the focused doc with the default symbol. (In production no doc is focused at this
        200ms tick — restored docs use make_current=False — so the old call was a silent no-op; but
        under slow parallel test load it fired mid-test and clobbered a just-focused doc to the
        default 'BTCUSDT' — a real latent bug that surfaced as a panels-suite flake.)"""
        if self._closing:
            return
        if self._session and self._bars and self.studio_price is not None:
            # Studio chart only exists while its dock is open: a restored "studio" tool recreates
            # it (open_tools restore runs before this), otherwise studio_price is None — skip it.
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
        doc = ChartDocument(symbol, interval or self._interval)
        self._doc_widgets.append(doc)
        self._live_hub.register(doc)
        doc.set_bus(self._link_bus)        # join symbol link groups (colour set via its dot)

        self._make_chart_frame(doc, state=state, make_current=make_current)
        doc.load(network=network)
        if state:
            doc.apply_state(state)
        return doc

    def _make_chart_frame(self, doc, *, state: dict | None = None, make_current: bool = True):
        """Wrap an (already-registered) ChartDocument in a clean ChartWindowFrame, wire its
        signals, place + show it, and paint its feed badge. Shared by _new_chart_document (a fresh
        doc) and _detach_chart_dock (tear a docked chart back out — the doc is already loaded)."""
        from .chartwin import ChartWindowFrame

        frame = ChartWindowFrame(doc, self.dock_manager)
        frame.closed.connect(lambda f: self._on_chart_window_closed(f))
        frame.activated.connect(self._on_chart_window_activated)
        frame.cloneRequested.connect(self._clone_window)
        frame.redockRequested.connect(self._redock_chart)
        frame.minimizeRequested.connect(self._minimize_chart_window_to_left)
        # pairs indicators need a 2nd symbol the app fetches (the chart can't reach the data
        # layer). The central chart used to wire this; now each peer frame's chart does.
        doc.chart.pairsRequested.connect(lambda n, ch=doc.chart: self._add_pairs(ch, n))
        self._chart_frames.append(frame)
        # cascade placement: each new window steps down-right from the last
        n = len(self._chart_frames) - 1
        frame.move(36 + (n % 8) * 34, 24 + (n % 8) * 28)
        if state and isinstance(state.get("geometry"), list) and len(state["geometry"]) == 4:
            x, y, w, h = (int(v) for v in state["geometry"])
            frame.setGeometry(x, y, max(320, w), max(160, h))
        frame.show()
        _fcolor, _fprefix = _FEED_STATES["live" if self._live_hub.is_live() else "cached"]
        frame.set_feed(_fcolor, _fprefix.replace(" · ", "").strip())
        self._update_feed_health()   # keep the status-bar badge in sync with the hub-driven header
        if make_current:
            frame.raise_()
            self._on_chart_window_activated(frame)
        return frame

    # --- chart dock/undock (symmetric with tools; "Dock into workspace") --------------------
    def _redock_chart(self, frame):
        """'Dock into workspace' on a chart window — reparent the LIVE ChartDocument into a clean
        ADS dock (no reload, no state loss; the doc stays registered with the live hub + link bus).
        Tear it back out via the dock's ⧉ (-> _detach_chart_dock)."""
        if frame not in self._chart_frames:
            return None
        doc = frame.take_body()
        self._chart_frames.remove(frame)
        if self._active_frame is frame:
            self._active_frame = None
        frame.dispose()                      # drop the frame; no close-driven unregister
        if doc is None:
            return None
        from .toolreg import make_chart_dock

        self._chart_seq += 1
        name = f"chart:{self._chart_seq}"
        dock = make_chart_dock(self.dock_manager, doc, name, icon=self._tool_icon("chart"))
        # Tile it VISIBLY to the right of the central chart (so it appears alongside, not hidden as
        # a tab behind it); fall back to the central area if that can't be resolved.
        base = None
        try:
            sd = self.tabs.dock(0)
            if sd is not None and not sd.isClosed():
                base = sd.dockAreaWidget()
        except (RuntimeError, AttributeError, IndexError):
            base = None
        if base is not None:
            self.dock_manager.addDockWidget(QtAds.RightDockWidgetArea, dock, base)
        else:
            self.dock_manager.addDockWidget(QtAds.CenterDockWidgetArea, dock)
        self._chart_docks[name] = dock
        dock.closed.connect(self._make_chart_dock_close_handler(name, doc))
        dock.toggleView(True)
        dock.raise_()
        return dock

    def _make_chart_dock_close_handler(self, name: str, doc):
        """The dock ``closed`` slot for a docked chart. A close that is really a tear-out (name in
        _chart_detaching — the doc lives on in a window) only drops the dock ref; a real close
        unregisters the doc (live hub / link bus / _doc_widgets) like a chart window close."""
        def _on_closed(n=name, d=doc):
            self._chart_docks.pop(n, None)
            if n in self._chart_detaching:
                self._chart_detaching.discard(n)
                return
            self._on_document_closed(d)
        return _on_closed

    def _detach_chart_dock(self, name: str):
        """⧉ on a docked chart — tear the LIVE ChartDocument back out to a clean window."""
        dock = self._chart_docks.get(name)
        if dock is None or dock.isClosed():
            return None
        doc = dock.takeWidget()
        if doc is None:
            return None
        self._chart_detaching.add(name)
        dock.closeDockWidget()               # remove the empty dock (handler skips unregister)
        return self._make_chart_frame(doc)

    def _close_all_chart_docks(self) -> None:
        for dock in list(self._chart_docks.values()):
            try:
                dock.closeDockWidget()
            except Exception:  # noqa: BLE001 - teardown best-effort
                pass

    def _clone_window(self, frame) -> None:
        """Duplicate a chart window — same symbol / interval / indicators / link groups, cascaded
        to a fresh window. Reuses the copy/paste state capture (no clipboard round-trip)."""
        st = self._doc_state_with_geometry(frame.doc)
        st.pop("geometry", None)   # let the new window cascade instead of stacking exactly
        self._new_chart_document(st.get("symbol", frame.doc.symbol),
                                 st.get("interval", frame.doc.interval), state=st)

    def _restack_left_rail(self) -> None:
        """AmiBroker-style hide: lay out every rolled-up (attached) window as a title stub stacked
        down the LEFT edge of the workspace. Called by ChartWindowFrame.toggle_rollup so clicking ─
        on a tool/chart window collapses it to the left rail; clicking ─ again restores it and the
        remaining stubs re-stack. Detached OS windows are left where they are (skipped)."""
        y = 6
        for f in (self._chart_frames + list(self._tool_frames.values())
                  + list(self._panel_frames.values())):
            try:
                if not f.is_detached() and getattr(f, "_rolled", False):
                    f.move(6, y)
                    f.raise_()
                    y += f.height() + 4
            except RuntimeError:        # frame mid-teardown — skip
                continue

    def _maximize_dock(self, target) -> None:
        """Unified maximize/restore for any side panel: make ``target`` fill the workspace by hiding
        every OTHER live dock (side panels, docked charts / tools), keeping the app chrome. Toggling
        the same target's □ (or Esc) restores everything. If there is nothing to hide (the target
        already fills the workspace) it is a deliberate no-op, so the button never lands in a false
        maximized state."""
        if target is None:
            return
        if self._maximized is target:                  # same target -> toggle off
            self._restore_maximized()
            return
        if self._maximized is not None:                # a different dock is maximized -> undo first
            self._restore_maximized()
        to_hide = [d for d in self._all_live_docks()
                   if d is not target and not self._dock_closed(d)]
        if not to_hide:
            return                                     # already fills the workspace -> no-op
        self._maximized = target
        self._max_hidden = []
        for d in to_hide:
            self._max_hidden.append(d)
            try:
                self._set_dock_open(d, False)
            except RuntimeError:
                continue
        self._panel_maxed = target.objectName()        # compat flag the dockshell glyph code reads
        self._sync_max_glyph(target, True)
        self._fit_chart_header()

    def _restore_maximized(self) -> None:
        """Undo _maximize_dock: re-show every hidden dock, reset glyph+flags."""
        if self._maximized is None:
            return
        for d in self._max_hidden:
            try:
                self._set_dock_open(d, True)
            except RuntimeError:                       # a hidden dock was torn down meanwhile
                continue
        self._max_hidden = []
        target = self._maximized
        self._maximized = None
        self._panel_maxed = None
        self._sync_max_glyph(target, False)
        self._fit_chart_header()

    def _all_live_docks(self) -> list:
        """The chart space + every side panel + docked chart + docked tool (deduped, None-safe)."""
        docks = []
        cs = self._chart_space_dock()
        if cs is not None:
            docks.append(cs)
        for src in (self._panel_dock_map.values(), self._chart_docks.values(),
                    self._tool_docks.values()):
            for d in src:
                if d is not None and d not in docks:
                    docks.append(d)
        return docks

    def _dock_closed(self, dock) -> bool:
        try:
            return dock.isClosed()
        except RuntimeError:
            return True

    def keyPressEvent(self, event):  # noqa: N802 - Esc un-maximizes (brings everything back)
        if event.key() == QtCore.Qt.Key_Escape and self._maximized is not None:
            self._restore_maximized()
            event.accept()
            return
        super().keyPressEvent(event)

    def _max_button_for(self, dock):
        """The □/❐ maximize button for any dock: the chart-space HEADER for the chart space, else
        the dock's own title-bar header. None if unavailable."""
        try:
            if dock is not None and dock is self._chart_space_dock():
                h = self.tabs.header_widget()
                return h.button("max") if (h is not None and hasattr(h, "button")) else None
            hdr = dock.dockAreaWidget().titleBar()._header
            return hdr.button("max") if hasattr(hdr, "button") else None
        except (RuntimeError, AttributeError):
            return None

    def _sync_max_glyph(self, dock, maxed: bool) -> None:
        """THE single place that flips a dock's maximize glyph □↔❐ (+ tooltip). Shared by the chart
        header AND every panel, so the two glyph paths can't drift apart (the recurring icon-desync
        bug came from having two copies of this)."""
        b = self._max_button_for(dock)
        if b is not None:
            b.setText("❐" if maxed else "□")
            b.setToolTip("Restore" if maxed else "Maximize / restore")

    def _clear_chart_maxed(self) -> None:
        """Defensive: when the chart or a panel is restored from the MINIMIZE rail, make sure no
        stale chart-maximize state/glyph lingers (minimize must never fight maximize). Clears the
        unified state only if the CHART was the maximized one, and normalises the chart glyph."""
        if self._maximized is self._chart_space_dock():
            self._maximized = None
            self._max_hidden = []
        self._sync_max_glyph(self._chart_space_dock(), False)

    def _frame_of(self, doc) -> "object | None":
        for f in self._chart_frames:
            if f.doc is doc:
                return f
        return None

    def _on_chart_window_closed(self, frame) -> None:
        if frame in self._chart_frames:
            self._chart_frames.remove(frame)
        if self._active_frame is frame:
            self._active_frame = None          # symbol box falls back to the central chart
        self._on_document_closed(frame.doc)
        # NB: no re-tile — the remaining windows stay exactly where the user left them.

    def _on_chart_window_activated(self, frame) -> None:
        self._set_active_frame(frame)

    def _set_active_frame(self, frame) -> None:
        """Track the FOCUSED chart window. The symbol box / watchlist drive whichever chart is
        focused; ``None`` means no chart is focused (so the symbol box no-ops). There is no
        central chart — `self.price` simply IS the focused frame's chart, so every chart-state
        read (export, copy, indicator capture, header ticker) follows the focused window."""
        self._active_frame = frame
        try:
            self.price = frame.doc.chart if frame is not None else None
        except (RuntimeError, AttributeError):
            self.price = None
        for f in self._chart_frames:
            try:
                f.set_active(f is frame)
            except RuntimeError:               # a frame torn down between activation and here
                pass

    def _active_chart_doc(self):
        """The focused chart window's ChartDocument, or ``None`` when the central chart is the
        target (nothing else focused). A rolled-up / disposed frame is never the target."""
        f = self._active_frame
        if f is not None and f in self._chart_frames:
            try:
                if f.isVisible() and not f._rolled:
                    return f.doc
            except RuntimeError:
                pass
        return None

    def _close_all_chart_windows(self) -> None:
        for f in list(self._chart_frames):
            f.close_window()

    def _arrange_chart_windows(self, mode: str) -> None:
        """Window ▸ Arrange: tidy EVERY chart/tool the user has open.

        - Floating chart windows + torn-out tool windows (all chartwin frames) are geometry-tiled
          by chartwin.arrange (grid / rows / columns / cascade).
        - Any tool/chart docked INTO the workspace ("Dock into workspace") is ADS-tiled by
          SpaceDeck.arrange_docks (docking has no cascade, so cascade falls back to grid).
        There is no central chart anymore — every chart is a floating peer, so a uniform tile is
        exactly what the user expects.
        """
        from . import chartwin

        self._last_arrange_mode = mode      # record the last Arrange mode the user chose
        frames = self._chart_frames + list(self._tool_frames.values())
        def _alive(d):
            # isClosed() raises if the C++ dock was already freed (a leaked dock under xdist, or a
            # close racing the arrange) — treat a dead/raising dock as not-live.
            try:
                return d is not None and not d.isClosed()
            except RuntimeError:
                return False

        # Tile the floating chart/tool WINDOWS (the common case — the user's primary charts).
        chartwin.arrange(frames, self.dock_manager, mode)
        # AND tidy any tool/chart "windows" docked INTO the workspace ("Dock into workspace") —
        # same grid. There's no central chart anchor anymore, so this uses arrange_docks (which
        # needs no anchor); the side panels (Market watch/Trades) keep their natural dock edges.
        docks = [d for d in (*self._tool_docks.values(), *self._chart_docks.values()) if _alive(d)]
        if docks:
            self.tabs.arrange_docks(docks, "grid" if mode == "cascade" else mode)

    def open_tool(self, key: str):
        """Open the tool for ``key`` as its own floating window, or focus it if already open.

        SINGLETON: re-opening the same key focuses the existing window (or dock, if the user has
        since docked it via "Dock into workspace") instead of creating a second one.

        MT-style (the user's choice over MultiCharts-style dock-anywhere): a tool is its OWN
        window, not a dock — independent, multi-monitor friendly, and free of the ADS dock-area
        split/collapse teardown race that was silently deleting docked tools.
        """
        # The tool may already be open as a floating WINDOW — focus it (and un-minimize it from the
        # left rail if it was parked there: the frame is just hidden, so show + drop its rail tab).
        frame = self._tool_frames.get(key)
        if frame is not None:
            frame.show()
            frame.raise_()
            frame.activated.emit(frame)
            self._min_rail.remove(key)
            return frame
        existing = self._tool_docks.get(key)
        if existing is not None:
            try:
                alive = not existing.isClosed()
            except RuntimeError:        # DeleteOnClose dock destroyed without notifying us
                alive = False
                self._tool_docks.pop(key, None)
            if alive:
                existing.toggleView(True)
                area = existing.dockAreaWidget()
                if area is not None:
                    area.setCurrentDockWidget(existing)
                existing.raise_()
                return existing
        # MT-style: a tool opens as its OWN floating window (ToolWindowFrame), NOT a dock — each
        # tool is independent, so there's no dock-area split/tab/collapse teardown race (the bug
        # that silently deleted tools). It can still be docked later via the window's "Dock into
        # workspace" verb. Reuses the tested session-restore window path (_open_tool_window).
        return self._open_tool_window(key)

    def _build_tool_widget(self, key: str):
        """Create a fresh tool widget from the registry, mirror it onto its legacy alias (so
        existing readers — signals, set_symbol, dashboard-tile seeding — keep working while it's
        open), and run its one-time signal wiring. Shared by open_tool + _open_tool_window."""
        from .toolreg import ToolRegistry

        widget = ToolRegistry.create(key, self)
        attr = _TOOL_ATTR.get(key)
        if attr:
            setattr(self, attr, widget)
        self._wire_tool(key, widget)
        return widget

    def _make_tool_window(self, key: str, widget):
        """Wrap a LIVE tool widget in a clean ToolWindowFrame + wire its close/redock signals,
        and register it in _tool_frames. Shared by _detach_tool + _open_tool_window (restore)."""
        from .chartwin import ToolWindowFrame

        frame = ToolWindowFrame(widget, self.dock_manager,
                                title=toolreg.TOOL_LABELS.get(key, key),
                                icon=self._tool_icon(key).pixmap(16, 16))
        frame.closed.connect(lambda _f, k=key: self._on_tool_window_closed(k))
        frame.redockRequested.connect(lambda _f, k=key: self._redock_tool(k))
        frame.minimizeRequested.connect(lambda _f, k=key: self._minimize_tool_to_left(k))
        self._tool_frames[key] = frame
        return frame

    def _minimize_tool_to_left(self, key: str):
        """A tool's ─ : HIDE its window and park a vertical restore tab on the left rail (AmiBroker
        style); click the tab to restore it full-size. The frame stays alive (hidden) so there is no
        rebuild / state loss. Replaces ADS auto-hide (which deleted docks + left an empty-space
        flyout once several were minimized)."""
        frame = self._tool_frames.get(key)
        if frame is None:
            return
        frame.hide()
        self._min_rail.add(key, toolreg.TOOL_LABELS.get(key, key), self._tool_icon(key),
                           lambda k=key: self._restore_tool_from_rail(k))

    def _restore_tool_from_rail(self, key: str):
        self._min_rail.remove(key)      # idempotent — also drops the tab when called programmatically
        frame = self._tool_frames.get(key)
        if frame is None:               # closed while minimized — reopen fresh
            self.open_tool(key)
            return
        try:
            frame.show(); frame.raise_(); frame.activated.emit(frame)
        except RuntimeError:
            self._tool_frames.pop(key, None)

    def _minimize_chart_window_to_left(self, frame):
        """A chart WINDOW's ─ : hide it and park a restore tab on the left rail (like the tools)."""
        key = f"chartwin:{id(frame)}"
        doc = getattr(frame, "doc", None)
        label = doc.title() if (doc is not None and hasattr(doc, "title")) else "Chart"
        frame.hide()
        self._min_rail.add(key, label, self._tool_icon("chart"),
                           lambda f=frame: self._restore_chart_window(f))

    def _restore_chart_window(self, frame):
        self._min_rail.remove(f"chartwin:{id(frame)}")
        try:
            frame.show(); frame.raise_(); frame.activated.emit(frame)
        except RuntimeError:
            pass

    def _minimize_panel_to_rail(self, dock):
        """A side panel's ─ : hide the dock and park a restore tab on the left rail (AmiBroker)."""
        key = dock.objectName() or f"panel:{id(dock)}"
        label = dock.windowTitle() or key
        try:
            self._set_dock_open(dock, False)
        except RuntimeError:
            return
        self._min_rail.add(key, label, dock.icon(),
                           lambda d=dock: self._restore_panel_from_rail(d))

    def _restore_panel_from_rail(self, dock):
        try:
            self._min_rail.remove(dock.objectName())
            # A restored panel must coexist with the chart — if the chart was 'maximized' (panels
            # hidden), clear that state first so the panel isn't immediately re-hidden / leaving a
            # stale ❐ on the chart header. _clear_chart_maxed self-guards (no-op unless the chart
            # was the maximized dock), so it's safe to call unconditionally here.
            self._clear_chart_maxed()
            self._set_dock_open(dock, True)
            dock.raise_()
        except RuntimeError:
            pass

    def _tool_icon(self, key: str):
        """The colourful per-tool launcher icon (rail + dock + tool window), one place."""
        return icons.rail_icon(
            key, toolreg.tool_color(key, theme.TEXT3),
            toolreg.tool_color(key, theme.ACCENT),
            toolreg.tool_hover_color(key, theme.TEXT2))

    def _make_tool_close_handler(self, key: str, widget):
        """The dock ``closed`` slot for a tool. A close that is really a DETACH (the widget is
        being re-homed into a window — key in _tool_detaching) only drops the dock ref and skips
        teardown; a real close runs _teardown_tool. Shared by open_tool + _redock_tool."""
        def _on_tool_closed(k=key, w=widget):
            self._tool_docks.pop(k, None)
            if k in self._tool_detaching:        # detach in progress — widget lives on in a window
                self._tool_detaching.discard(k)
                return
            self._teardown_tool(k, w)
        return _on_tool_closed

    def _teardown_tool(self, key: str, widget) -> None:
        """Stop a tool's background work + drop its refs when it is truly closed (dock ✕ or its
        window ✕). Idempotent-ish best-effort; never blocks the close."""
        # Studio (the 8th tool) carries an AI worker + a lockstep chart fused to the pipeline:
        # wait the worker out (no destroyed-while-running) and rescue the eager replay controls
        # out of the DeleteOnClose dock tree BEFORE it is torn down, then nil studio_price so
        # _pipeline_charts() drops it. (studio_price itself lives in the dock tree -> destroyed.)
        if key == "studio":
            if hasattr(widget, "shutdown"):
                try:
                    widget.shutdown()
                except Exception:  # noqa: BLE001 - teardown best-effort; never block the close
                    pass
            self._rescue_studio_controls()
            self.studio_price = None
        attr = _TOOL_ATTR.get(key)
        if attr and getattr(self, attr, None) is widget:
            setattr(self, attr, None)   # clear the legacy alias (no dangling ref to a dead widget)
        # Stop any per-tool background work so no poller thread leaks.
        if hasattr(widget, "stop_feed"):
            try:
                widget.stop_feed()       # News poller thread
            except Exception:  # noqa: BLE001 - teardown best-effort; never block the close
                pass
        if key == "options" and getattr(self, "_options_svc", None) is not None:
            self._options_svc.stop_polling()   # the poller lives on the app-level service,
            self._options_started = False      # not the tab, so stop it here (re-arm next open)
            # The tab is about to be destroyed; an in-flight _FetchWorker QThread could still emit
            # into it (→ "C++ object already deleted" / 0xC0000409). Drop the svc->tab connections
            # NOW; re-armed by _wire_options on the next open via the _options_wired guard.
            for _sig in (self._options_svc.chainReady, self._options_svc.failed,
                         self._options_svc.expiriesReady):
                try:
                    _sig.disconnect()
                except (RuntimeError, TypeError):   # already gone — fine
                    pass
            self._options_wired = False

    def _detach_tool(self, key: str):
        """⧉ on a tool dock — tear the LIVE tool widget out of its ADS dock into a clean
        chartwin-style ``ToolWindowFrame`` (no rebuild, no state loss). The dock is closed with
        _tool_detaching set so its close handler skips teardown (the widget lives on)."""
        if key in self._tool_frames:                  # already a window — focus it
            self._tool_frames[key].raise_()
            return self._tool_frames[key]
        dock = self._tool_docks.get(key)
        if dock is None or dock.isClosed():
            return None
        widget = dock.takeWidget()                    # reparents the widget out; dock now empty
        if widget is None:
            return None
        self._tool_detaching.add(key)
        dock.closeDockWidget()                        # remove the empty dock (handler skips teardown)
        frame = self._make_tool_window(key, widget)
        n = len(self._tool_frames) - 1               # cascade like chart windows
        frame.move(80 + (n % 6) * 32, 64 + (n % 6) * 28)
        frame.show()
        frame.raise_()
        frame.activated.emit(frame)
        return frame

    def _open_tool_window(self, key: str, geometry=None):
        """Open a tool DIRECTLY as a clean floating window (session restore of a torn-out tool).
        Builds a fresh widget (state isn't persisted across restart — same as a restored dock)
        and places it at the saved attached geometry, or cascades when none was saved."""
        if key in self._tool_frames:
            return self._tool_frames[key]
        if key in self._tool_docks:            # already a dock this session — leave it docked
            return self._tool_docks[key]
        widget = self._build_tool_widget(key)
        frame = self._make_tool_window(key, widget)
        if geometry and len(geometry) == 4:
            x, y, w, h = (int(v) for v in geometry)
            frame.setGeometry(x, y, max(320, w), max(160, h))
        else:
            n = len(self._tool_frames) - 1
            frame.move(80 + (n % 6) * 32, 64 + (n % 6) * 28)
        frame.show()
        return frame

    def _tool_window_states(self) -> list:
        """Persist each torn-out tool window: its key + (for an attached frame) its host-relative
        geometry. A detached OS window's geometry is screen coords — omit it so it cascades on
        restore (mirrors _doc_state_with_geometry for chart windows)."""
        out = []
        for key, frame in self._tool_frames.items():
            spec = {"key": key}
            try:
                if not frame.is_detached():
                    g = frame.geometry()
                    spec["geometry"] = [g.x(), g.y(), g.width(), g.height()]
            except RuntimeError:   # frame mid-teardown — persist the key alone
                pass
            out.append(spec)
        return out

    def _redock_tool(self, key: str):
        """'Dock into workspace' on a tool window — reparent the LIVE widget back into a fresh
        ADS dock (state preserved; signals stay connected, so no re-wire). The frame is disposed
        WITHOUT its close handler (the widget has been re-homed).

        (Historical note: an earlier ``auto_hide_side`` arg routed the ─ minimize through
        ``addAutoHideDockWidget``. That's GONE — minimize uses the custom ``MinimizedRail`` (tools,
        panels and chart windows all park there), and ADS auto-hide is a teardown-crash footgun,
        upstream issue mborgerson/pyside6_qtads#31 — so the app never creates an auto-hide container.)"""
        from .toolreg import make_tool_dock

        frame = self._tool_frames.pop(key, None)
        if frame is None:
            return None
        widget = frame.take_body()
        frame.dispose()                              # drop the frame; no close-driven teardown
        if widget is None:
            return None
        dock = make_tool_dock(self.dock_manager, key, widget, icon=self._tool_icon(key))
        self.dock_manager.addDockWidget(QtAds.CenterDockWidgetArea, dock)
        self._tool_docks[key] = dock
        # Re-arm the close handler for the new dock (signals/alias from the first open still hold,
        # so _wire_tool is NOT re-run — that would double-connect).
        dock.closed.connect(self._make_tool_close_handler(key, widget))
        dock.toggleView(True)
        dock.raise_()
        return dock

    def _on_tool_window_closed(self, key: str) -> None:
        """A tool WINDOW was closed (✕) — run the same teardown a docked close would, then let
        the frame's dispose() drop the widget with it."""
        frame = self._tool_frames.pop(key, None)
        if frame is None:
            return
        widget = getattr(frame, "doc", None)         # body still child of the frame here
        if widget is not None:
            self._teardown_tool(key, widget)
        # NB: no re-tile — the remaining windows stay exactly where the user left them.

    def _close_all_tool_windows(self) -> None:
        """Dispose every torn-out tool window (used on app close / workspace swap). Teardown runs
        via _on_tool_window_closed (wired to each frame's closed signal)."""
        for frame in list(self._tool_frames.values()):
            frame.close_window()

    def _close_all_panel_windows(self) -> None:
        """Re-home every floated side panel (Market watch / Trades) back into its dock then close it
        (used on workspace swap / app close), so a floated panel is never orphaned when the layout
        is replaced. Teardown runs via _on_panel_window_closed (wired to each frame's closed)."""
        for frame in list(self._panel_frames.values()):
            try:
                frame.close_window()
            except RuntimeError:        # frame mid-teardown — skip
                pass

    def _close_all_tool_docks(self) -> None:
        """Close every DOCKED tool (calendars/screener/journal/news/options/data/studio). Each
        closeDockWidget fires _on_tool_closed -> _teardown_tool (stop_feed/shutdown/worker-join) +
        clears _tool_docks + the legacy alias. Without this, a tool DOCK (the default open path)
        survives teardown and its background timers/workers run on into manager destruction."""
        for dock in list(self._tool_docks.values()):
            try:
                dock.closeDockWidget()
            except RuntimeError:        # dock mid-teardown — skip
                pass

    def _close_all_frames_and_docks(self) -> None:
        """The ONE ordered sweep that closes every torn-out window + docked surface so NOTHING is
        left as an orphan top-level for an arbitrary-order GC/deleteLater to free (the teardown-race
        source). Order is the proven workspace-swap order: chart windows -> docked charts -> tool
        windows -> floated panels -> tool docks -> chart-document docks. Each close runs its handler's
        unregister/teardown. ONLY safe once shutdown() has set _closing + disconnected the re-entrant
        signals (a bare sweep heap-corrupts — see the closed PR #170)."""
        self._close_all_chart_windows()
        self._close_all_chart_docks()
        self._close_all_tool_windows()
        self._close_all_panel_windows()
        self._close_all_tool_docks()
        try:
            self.tabs.close_all_documents()
        except (RuntimeError, AttributeError):
            pass

    def _detach_panel(self, dock):
        """⧉ on a side panel (Market watch / Trades) — float its widget into a clean window. Panels
        are NOT DeleteOnClose, so the SAME dock is reused: take the widget out + hide the empty dock
        (rail stays on — it's floating, not closed), and put the widget back on redock/close."""
        from .chartwin import ToolWindowFrame
        fid = next((k for k, d in self._panel_dock_map.items() if d is dock), dock.objectName())
        if fid in self._panel_frames:
            self._panel_frames[fid].raise_()
            return self._panel_frames[fid]
        title, icon = dock.windowTitle(), dock.icon()
        widget = dock.takeWidget()
        if widget is None:
            return None
        self._set_dock_open(dock, False)             # hide the now-empty dock (guarded: no rail off)
        frame = ToolWindowFrame(widget, self.dock_manager, title=title,
                                icon=icon.pixmap(16, 16) if (icon and not icon.isNull()) else None)
        frame.closed.connect(lambda _f, f=fid: self._on_panel_window_closed(f))
        frame.redockRequested.connect(lambda _f, f=fid: self._redock_panel(f))
        self._panel_frames[fid] = frame
        n = len(self._panel_frames) - 1
        frame.move(100 + (n % 6) * 32, 80 + (n % 6) * 28)
        frame.show()
        frame.raise_()
        return frame

    def _redock_panel(self, fid: str):
        """'Dock into workspace' on a floated panel window — put the live widget back into its
        (reused, hidden) dock and re-show it."""
        frame = self._panel_frames.pop(fid, None)
        if frame is None:
            return
        widget = frame.take_body()
        frame.dispose()
        dock = self._panel_dock_map.get(fid)
        if dock is None or widget is None:
            return
        dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
        self._set_dock_open(dock, True)

    def _on_panel_window_closed(self, fid: str):
        """A floated panel window closed (✕) — re-home its widget into the (reused) dock and close
        the panel so the rail reflects it closed and the widget is never orphaned."""
        frame = self._panel_frames.pop(fid, None)
        if frame is None:
            return
        widget = getattr(frame, "doc", None)
        dock = self._panel_dock_map.get(fid)
        if dock is not None and widget is not None:
            dock.setWidget(widget, QtAds.CDockWidget.ForceNoScrollArea)
            dock.toggleView(False)                   # unguarded -> the rail shows the panel closed

    def _toggle_panel_maximize(self, dock):
        """□ on a panel — maximize it to fill the workspace (chart + other docks hidden, chart
        parked on the left rail), toggling back. Thin wrapper over the unified _maximize_dock."""
        self._maximize_dock(dock)

    def _chart_space_dock(self):
        """The central chart-space CDockWidget (space 0), or None if torn down."""
        try:
            return self.tabs.dock(0)
        except (RuntimeError, AttributeError, IndexError):
            return None

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
    # --- central charts + replay ---
    def _build_central(self):
        # Chart-unify keystone: NO docked central chart. The center ADS area hosts only the side
        # panels + any chart docked via "Dock into workspace"; the primary charts are floating
        # ChartWindowFrame peers. So there's no chart "card"/space to build here — just the dock
        # manager, the minimize rail, the unified-titlebar factory and the SpaceDeck facade.
        self._backtester = None   # was the central chart container; kept as a None sentinel
        configure_dock_manager_defaults()  # static config — must precede CDockManager()
        self.dock_manager = QtAds.CDockManager(self)  # installs itself as the central widget
        # Custom MINIMIZE rail (left of the central widget). The ─ verb hides a window/panel and
        # parks a vertical tab here; clicking restores it. Replaces ADS auto-hide (unstable with
        # several auto-hide containers — deleted docks + empty-space flyout). See ui/minrail.py.
        from .minrail import MinimizedRail
        self._min_rail = MinimizedRail(self)
        self.addToolBar(QtCore.Qt.LeftToolBarArea, self._min_rail)
        self._restore_cbs: dict[str, "callable"] = {}
        # Unified title bar (stage 1): our factory renders every dock-area title bar — the
        # central spaces area carries the single-title MC chart header; panels keep MC chrome.
        # Per-manager install (not the global setFactory) so offscreen tests + any future
        # manager keep ADS defaults. Keep a python ref so the factory isn't GC'd.
        from .dockshell import VikeComponentsFactory
        self._dock_factory = VikeComponentsFactory()
        self.dock_manager.setComponentsFactory(self._dock_factory)
        # Stage A1: ADS floating is disabled (it produced broken/double-chrome floats), so no
        # floatingWidgetCreated handler is wired — charts float cleanly via chartwin instead.
        self.dock_manager.setStyleSheet(dock_qss())
        self.tabs = SpaceDeck(self.dock_manager)
        # The factory recreates the chart-space header on relayout; each time it asks us to
        # re-cap its width to the panel edge (forced far-right ⧉ ─ □ ✕).
        self.tabs.set_fit_callback(self._fit_chart_header)
        # Chart-unify keystone: NO "Chart" space is added — SpaceDeck holds ZERO spaces. The
        # center hosts only side panels + any chart docked via "Dock into workspace". Charts open
        # as floating ChartWindowFrame peers (topbar "New chart" / Ctrl+N), so they all tile under
        # Window>Arrange. (set_fit_callback stays wired as the seam for a future docked chart.)
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
        # The 7 non-Studio tools (screener/journal/alerts/data/news/calendar/options) are NO
        # LONGER eager SpaceDeck spaces — they open on-demand as dock widgets via open_tool(key)
        # (empty-workspace re-arch). Their legacy attributes (self.screener/.datamanager/.news/…)
        # are set by open_tool ONLY while the tool dock is open, and cleared on close, so code that
        # reads them keeps working when the tool is live and stays guarded otherwise. SpaceDeck now
        # holds ZERO spaces (the central chart is gone). Per-tool signal wiring that used to live
        # here moved to _wire_tool(); the OptionsService stays app-level (eager).
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
        # Every tool has a title-bar launcher icon (the Go menu's tool list was dropped as a dupe);
        # journal + alerts included so they're not menu-only. Order matches the old Go menu.
        for icon_name, label, key in (("screener", "Screener", "screener"),
                                      ("journal", "Journal", "journal"), ("alerts", "Alerts", "alerts"),
                                      ("data", "Data", "data"), ("news", "News", "news"),
                                      ("calendar", "Calendar", "calendar"), ("options", "Options", "options")):
            self.topbar.add_launcher(icon_name, f"{label} window",
                                     lambda *_a, k=key: self.open_tool(k))
        # Task 5 — ExecArmBar: venue/product/env/leverage selectors + Arm/Disarm.
        # The widget is thin; armRequested emits None and MainWindow resolves the spec with the live
        # symbol. _restore_arm_selection restores the saved selection only (never auto-arms).
        from .exec_arm import ExecArmBar
        self.exec_arm = ExecArmBar()
        self.exec_arm.armRequested.connect(
            lambda _=None: self._on_arm_requested(
                # 6e: venue-guarded override: only use the option pick when the venue selector
                # is "deribit" — if the user flips venue to binance after picking an option, the
                # arm path falls back to self._symbol (the spot/perp chart symbol), NOT the option.
                self.exec_arm.current_spec(
                    (self._exec_symbol_override
                     if self.exec_arm._venue.currentText() == "deribit"
                     else None) or self._symbol
                )
            )
        )
        self.exec_arm.disarmRequested.connect(self._on_disarm_requested)
        self._restore_arm_selection()   # QSettings restore — NEVER auto-arms
        # Mount the arm bar as an always-visible top toolbar — the production surface that finally
        # makes live exec user-reachable (it was env-var-only). NOT the lazily-re-parented Studio
        # strip: exec-arm is a GLOBAL control. setMovable(False) keeps it stable below the title bar.
        self._exec_toolbar = QtWidgets.QToolBar("Execution", self)
        self._exec_toolbar.setObjectName("exec_arm_toolbar")
        self._exec_toolbar.setMovable(False)
        self._exec_toolbar.addWidget(self.exec_arm)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self._exec_toolbar)

        # Live order ticket — the production caller of LiveOmsHub.submit_ticket (was unreachable).
        from .order_ticket import OrderTicket
        from ..exec.coid import CoidMinter
        from ..exec.order_ticket import OrderTicketStatus
        self._coid = CoidMinter()
        self._ticket_status = OrderTicketStatus()
        self._armed_env: str = ""          # set in _on_arm_requested; read in _confirm_order (single source of truth)
        self.order_ticket = OrderTicket()
        self.order_ticket.submitRequested.connect(self._on_submit_ticket)
        self._order_ticket_toolbar = QtWidgets.QToolBar("Order ticket", self)
        self._order_ticket_toolbar.setObjectName("order_ticket_toolbar")
        self._order_ticket_toolbar.setMovable(False)
        self._order_ticket_toolbar.addWidget(self.order_ticket)
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self._order_ticket_toolbar)

        # Live Positions & Open-Orders panel — reads the armed hub's Account + registry, refreshed by
        # the SAME _on_exec_event main-thread subscriber; per-row Cancel -> _on_cancel_ticket.
        from .positions_panel import PositionsPanel
        self.positions_panel = PositionsPanel()
        self.positions_panel.cancelRequested.connect(self._on_cancel_ticket)

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
        # Capture the chart explicitly: self.studio_price is niled on dock close, so a late
        # pairsRequested must NOT resolve self.studio_price by name (-> _add_pairs(None) crash).
        self.studio_price.pairsRequested.connect(
            lambda n, ch=self.studio_price: self._add_pairs(ch, n))
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

    def _on_option_instrument_chosen(self, instrument_name: str) -> None:
        """6e: A Deribit options-chain row was picked for trading — pre-stage the arm bar to
        deribit/Option/<instrument_name> and stash it as the symbol the NEXT Arm uses.

        Does NOT arm, does NOT open a dock, does NOT auto-arrange (the user clicks Arm).
        Inert for non-Deribit/equity picks (pick_to_arm_selection returns None).
        If already armed (live hub can't be retargeted), reports to the status line and returns
        WITHOUT mutating _exec_symbol_override (no auto-disarm — the user disarms first).
        """
        from ..exec.arm_select import pick_to_arm_selection

        sel = pick_to_arm_selection(instrument_name)
        tab = getattr(self, "options", None)
        if sel is None:
            if tab is not None:
                tab.set_status("Not a tradable Deribit option — pick a Deribit chain row")
            return
        if getattr(self, "_exec_session", None) is not None:
            # Per-instrument hub: a live session can't be retargeted. Report; the user disarms first.
            if tab is not None:
                tab.set_status(f"Disarm to switch contract (armed; picked {sel.symbol})")
            return
        self._exec_symbol_override = sel.symbol
        self.exec_arm.set_selection(
            venue=sel.venue, product=sel.product,
            environment=self.exec_arm._env.currentText(), leverage=1)
        if tab is not None:
            tab.set_status(f"Selected {sel.symbol} — click Arm (deribit / Option)")

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
        tab.instrumentChosen.connect(self._on_option_instrument_chosen)   # 6e: chain-row pick
        self._load_options_underlying = _load_underlying
        # Options-as-dock: start the fetch right after wiring (the old space-switch lazy-start site
        # in _on_tab_changed is now inert — it keyed on the retired Options space).
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

    # Navigation after the chart-unify keystone:
    #  * SPACES — NONE. The central chart is gone; SpaceDeck holds zero eager spaces.
    #  * TOOLS — the 8 on-demand docks (studio/screener/…/options). Opened via open_tool(tool_key).
    #  * CHARTS — floating ChartWindowFrame peers, opened via the topbar "New chart" / Ctrl+N.
    _SPACE_ITEMS: list = []                                         # (glyph, name, space_index)
    _TOOL_ITEMS = [("✦", "Studio", "studio"),
                   ("⊞", "Screener", "screener"), ("☰", "Journal", "journal"),
                   ("◉", "Alerts", "alerts"), ("◈", "Data", "data"),
                   ("📰", "News", "news"), ("▦", "Calendar", "calendar"),
                   ("⊗", "Options", "options")]                      # (glyph, name, tool_key)

    # PANELS section of the rail: independent show/hide toggles (TradeLocker style).
    # (key, icon_name, tooltip, shortcut). All map to docks in _panel_dock_map. (The old
    # "backtester" entry that toggled the central chart is gone — there is no central chart.)
    _PANELS = [
        ("market", "market", "Market watch", "Ctrl+M"),
        ("trades", "trades", "Trades & Positions", "Ctrl+T"),
        ("positions", "trades", "Positions & Orders", "Ctrl+P"),
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
            key = name.lower()
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(
                key, theme.TEXT3,
                toolreg.tool_color(key, theme.ACCENT),
                toolreg.tool_hover_color(key, theme.TEXT2)))
            b.setIconSize(QtCore.QSize(28, 28))
            b.setToolTip(self._chip_tip(name))
            b.setCheckable(True)
            b.setFixedSize(46, 46)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(btn_qss)
            b.clicked.connect(lambda _c, idx=space_index: self.tabs.show_space(idx))
            self._rail_group.addButton(b, space_index)
            col.addWidget(b, 0, QtCore.Qt.AlignHCenter)
        # TOOLS (screener/…/options): each opens an on-demand dock — NOT part of the exclusive
        # space group (opening a tool dock doesn't change the current SPACE), so just action buttons.
        # Each tool gets its OWN distinct colour (toolreg.TOOL_COLORS) so the rail reads at a glance.
        # These buttons aren't checkable, so the RESTING (off) colour is what shows — use the tool
        # colour there too (not the dim TEXT3), with a lightened hover variant.
        for _glyph, name, tool_key in self._TOOL_ITEMS:
            _tc = toolreg.tool_color(tool_key, theme.ACCENT)
            b = QtWidgets.QToolButton()
            b.setIcon(icons.rail_icon(
                tool_key, _tc, _tc,
                toolreg.tool_hover_color(tool_key, theme.TEXT2)))
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
        # Tint the "+" in the chart launcher colour (matches the chart space icon + topbar New-chart).
        _chart_c = toolreg.tool_color("chart", theme.ACCENT)
        new_chart.setStyleSheet(
            f"QToolButton{{background:transparent;border:none;border-radius:13px;"
            f"color:{_chart_c};font-size:22px;}}"
            f"QToolButton:hover{{background:transparent;color:{toolreg.tool_hover_color('chart', theme.TEXT2)};}}"
        )
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
        if self._closing:
            return
        self._update_chart_header()
        if index < 0:        # a chart document (not a space) became current — nothing to do
            return
        news = getattr(self, "news", None)
        if news is not None and self.tabs.widget(index) is news:
            news.start_feed(self._symbol)

    def _update_chart_header(self) -> None:
        """Drive the chart-space header title (the single MC-style line) to the current space's
        name. SpaceDeck holds zero spaces post-keystone, so this early-returns (currentIndex==-1)
        unless a chart is ever docked via "Dock into workspace"; kept as the live seam for that."""
        if not hasattr(self, "tabs"):
            return
        idx = self.tabs.currentIndex()
        if idx < 0:                      # a chart document is current — leave the header as-is
            return
        name = (self._SPACE_ITEMS[idx][1] if idx < len(self._SPACE_ITEMS)
                else self.tabs.tabText(idx))
        icon_name = name.lower()
        self.tabs.set_header_title(name)
        try:
            self.tabs.set_header_icon(
                icons.rail_icon(icon_name, theme.ACCENT, theme.ACCENT, theme.ACCENT)
                .pixmap(16, 16))
        except Exception:  # noqa: BLE001 - icon is cosmetic; never block the header on it
            pass
        self._fit_chart_header()

    def _fit_chart_header(self) -> None:
        """Cap the chart-space header to the VISIBLE chart width so its ⧉ ─ □ ✕ sit at the
        chart's right edge. The central dock area extends BEHIND the side panels (ADS), so the
        title bar's own width is useless here — instead measure where the right-hand panels
        actually start (their global left edge) and cap to that. Re-run on resize / panel
        toggle / space change / header recreation."""
        if self._closing:
            return
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

    def _reclaim_floating_docks(self) -> None:
        """Defence after CDockManager.restoreState(): native ADS floating is retired, but a stale
        session / named-workspace blob can still describe a dock as a native CFloatingDockContainer,
        which ADS resurrects on restore (the inconsistent native 'Chart' window that can't be raised
        above our chartwin frames and hides-instead-of-closes). Pull every floating dock back into
        the central area and dispose the now-empty husks, so chartwin is the ONLY float path."""
        mgr = getattr(self, "dock_manager", None)
        if mgr is None:
            return
        try:
            floats = list(mgr.floatingWidgets())
        except (RuntimeError, AttributeError):
            floats = []
        # The loops below are no-ops when `floats` is empty.
        # a docked area to re-home into (prefer one that isn't itself floating)
        central = None
        for d in mgr.dockWidgets():
            try:
                if not d.isFloating() and d.dockAreaWidget() is not None:
                    central = d.dockAreaWidget()
                    break
            except RuntimeError:
                continue
        for container in floats:
            try:
                docks = list(container.dockWidgets())
            except (RuntimeError, AttributeError):
                docks = []
            for d in docks:
                try:
                    # A side PANEL belongs back on the RIGHT rail, not tabbed full-width into the
                    # center (which would shove the chart aside). Tools/charts re-home to center.
                    if d.objectName().startswith("panel:"):
                        mgr.addDockWidget(QtAds.RightDockWidgetArea, d)
                    elif central is not None:
                        mgr.addDockWidget(QtAds.CenterDockWidgetArea, d, central)
                    else:                                  # nothing docked yet — seed the central area
                        mgr.addDockWidget(QtAds.CenterDockWidgetArea, d)
                        central = d.dockAreaWidget()
                except (RuntimeError, TypeError):
                    continue
        # dispose any now-empty floating husk (its content was re-homed above)
        for container in list(mgr.floatingWidgets()):
            try:
                if not list(container.dockWidgets()):
                    container.deleteLater()
            except (RuntimeError, AttributeError):
                pass
        # (An auto-hide un-pin pass used to live here for a stale blob that restored an edge-pinned
        # dock. Removed: the app never creates ADS auto-hide containers — minimize is the custom left
        # rail, the dead addAutoHideDockWidget path went in #181 — and the v4 migration drops pre-rail
        # blobs, so the scenario is unreachable. It also could not be tested without creating a real
        # auto-hide container, which is the ADS teardown crash 0xC0000409 / upstream #31.)

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
        # Chart windows run their live feed on the LiveHub (async-load re-arch), NOT the old
        # _live_timer — so trust the hub's is_live() exactly as the chart header does (see app.py
        # ~538). Without this the header reads ● LIVE while this status badge wrongly stayed grey.
        hub = getattr(self, "_live_hub", None)
        if hub is not None and hub.is_live():
            self._set_feed_health("live")
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

    def _funding_tick(self) -> None:
        """Poll each REST funding poller on the MAIN thread (single-writer); publish on the bus.
        No-op when no live-exec funding poller is registered."""
        if self._closing:
            return
        for poller in self._funding_pollers:
            poller.poll()   # synchronous signed GET + main-thread bus.publish (no worker thread)

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
        if self._forward is not None or not self._bars or self._closing:
            return
        if self._live_fetch_for != (self._symbol, self._interval):
            return
        self._live_fail_streak = 0
        merged, appended, replaced_last = merge_live_bars(self._bars, fetched)
        try:
            if appended or replaced_last:
                was_at_end = self._replay.at_end
                self._bars = merged
                self._replay.n_bars = len(merged)
                self.slider.setMaximum(self._replay.last_index)
                overlays = self._strategy_factory().chart_overlays([b.close for b in merged])
                for ch in self._pipeline_charts():  # pipeline is Studio-only (no central chart)
                    ch.apply_live(merged, overlays, repaint=False)
                self._update_chart_header()   # live ticker: header last price + change% (overcome MC)
                if was_at_end:  # following the live edge -> advance the cursor and repaint
                    self._replay.seek(self._replay.last_index)
                    self._render_frame()
                    self.foot_info.setText(
                        f"{self._symbol} · {self._interval} · {len(merged):,} bars"
                    )
            self._update_feed_health()
        except RuntimeError:
            # The chart space (and its slider / footer) was torn down while this live fetch was in
            # flight — the live timer outran teardown. Skip the repaint rather than crash on the
            # deleted C++ widget (the same mid-teardown guard used across the dock title bars).
            return

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

        positions = make_panel_dock(self.dock_manager, "POSITIONS & ORDERS",
                                    self.positions_panel, QtAds.BottomDockWidgetArea,
                                    icon=_ico("trades"))

        # rail PANELS toggle targets (key must match _PANELS)
        self._market_dock = market
        self._trades_dock = trades
        self._panel_dock_map = {"market": market, "trades": trades, "movers": movers,
                                "pnl": pnl, "ecal": ecal, "headlines": headlines,
                                "positions": positions}
        self._docks = [market, trades, movers, pnl, ecal, headlines, positions]
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
        """Refresh the Trades & Positions summary strip from the current result.

        Wrapped against RuntimeError: a result update can land after the Trades panel was torn down
        (workspace swap / window close) — the ``_acct`` dict still holds the now-deleted QLabel C++
        objects, and setText on them raised mid-slot and could escalate to a worker crash under
        parallel teardown (test_controls_survive_studio_close on CI)."""
        if not hasattr(self, "_acct"):
            return
        try:
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
        except RuntimeError:
            pass   # Trades panel torn down mid-update — drop this refresh

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
                                        "headlines", "positions")
            btn.setChecked(bool(saved.get(key, fresh_default)))

    def _set_dock_open(self, dock, on: bool) -> None:
        """Programmatic open/close of an ADS panel dock. ``toggleView`` is the ADS way (plain
        setVisible desyncs its internal state); the guard keeps the resulting viewToggled
        signal from feeding back into the rail toggle bookkeeping. The guard SAVES/RESTORES its
        prior value rather than hard-clearing it, so a nested call (e.g. inside the restoreState
        guarded region) can't drop an outer guard on unwind."""
        if self._closing:
            return   # teardown: never toggleView a dock while ADS is destroying the graph
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
        if self._syncing_docks or self._closing:
            return
        self._panel_visible[key] = on
        btn = self._panel_btns.get(key)
        if btn is not None and btn.isChecked() != on:
            btn.blockSignals(True)   # reflect state only — _toggle_panel already ran or must not
            btn.setChecked(on)
            btn.blockSignals(False)

    def _toggle_panel(self, key: str, on: bool) -> None:
        # Panels are independent dock toggles now (no central chart to gate them on). Each just
        # opens/closes its own dock; the remembered visibility persists to the session.
        if self._closing:
            return
        self._panel_visible[key] = on
        dock = self._panel_dock_map.get(key)
        if dock is not None:
            self._set_dock_open(dock, on)

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
        if self._forward is not None or self._closing:
            return
        try:
            refresh_pinned(DEFAULT_ROOT, load_pins(_PINS_PATH))
        except Exception:  # noqa: BLE001 - transient read/write; retried next tick
            pass

    def _on_tab_changed(self, index: int) -> None:
        """Reconcile the center area + panels after a center-tab change.

        There are no spaces now; the center hosts only docked charts (rare; "Dock into workspace")
        or is empty. Side panels are INDEPENDENT toggles — always shown per their remembered
        visibility, never gated on a (gone) central chart. ``index`` is -1 when nothing/ a chart
        DOCUMENT is current.
        """
        # Re-entrancy guard: toggling a panel dock here can drive ADS to re-emit the center
        # area's currentChanged synchronously and re-enter this slot — an unbounded close/reopen
        # loop (a verified stack overflow when a panel is dropped onto the spaces tab strip).
        if getattr(self, "_in_tab_change", False) or self._closing:
            return
        self._in_tab_change = True
        try:
            current = self.tabs.currentWidget()
            on_document = isinstance(current, ChartDocument)
            self.tabs.setVisible(True)   # center always visible (docked charts / empty)
            # Panels honor their own remembered toggle, regardless of center content.
            for key, dock in getattr(self, "_panel_dock_map", {}).items():
                self._set_dock_open(dock, self._panel_visible.get(key, True))
            btn = self._rail_group.button(index)  # keep the icon rail in sync with the tabs
            if btn is not None:
                btn.setChecked(True)
            # the OS title bar follows the focused docked chart document (if any)
            if on_document:
                current.ensure_loaded()  # restored docs are cache-only until first focused
                self.setWindowTitle(f"vike-trader-app   {current.title()}")
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
        """Charts the backtest/replay/live pipeline feeds: ONLY the Studio chart (when its dock is
        open). After the chart-unify keystone there is no central chart in the pipeline — the
        floating ChartWindowFrame peers are independent (their OWN symbol/data, fed by the LiveHub),
        so a backtest must never inject trades/overlays into whatever chart happens to be focused.
        ``self.price`` (the focused frame's chart) is used for state reads only, never the pipeline."""
        return [c for c in (self.studio_price,) if c is not None]

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
            # The pipeline is Studio-only (no central chart after the chart-unify keystone), so the
            # backtest's trades + overlays go straight to the Studio chart. Trades + the SMA legend
            # are Studio-only; floating ChartWindowFrame peers keep their own clean view.
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
        doc = self._active_chart_doc()
        if doc is not None:
            # Stage 1 of chart unification: the symbol box / watchlist drive the FOCUSED chart
            # WINDOW, not the central chart. The ChartDocument loads itself (its own cache/network
            # + bars); the central-chart path below runs only when no chart window is focused.
            if getattr(self, "news", None) is not None:
                self.news.set_symbol(symbol)
            doc.load(symbol=symbol, interval=interval or doc.interval)
            return
        if getattr(self, "news", None) is not None:   # forward to the News tool only while open
            self.news.set_symbol(symbol)
        # No chart is open (central chart closed AND no focused window): the symbol box does NOTHING
        # — the chart is an ordinary dock now, not auto-opened/resurrected (chart unification). This
        # also makes an empty saved workspace stay empty: a session that restored the chart closed
        # means the startup auto-load no-ops here instead of forcing the chart back open.
        cs = self._chart_space_dock()
        if cs is None or cs.isClosed():
            return
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
                    f"{self._symbol}  ·  {self._interval}  ·  {last:,.2f}  ·  {len(self._bars)} bars"
                )

    # --- replay wiring ---
    def _render_frame(self):
        if self._closing:
            return
        i = self._replay.index
        for ch in self._pipeline_charts():
            ch.show_upto(i)
        self.pos_label.setText(f"bar {i} / {self._replay.last_index}")
        if self.slider.value() != i:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)

    def _on_tick(self):
        if self._closing:
            return
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

        self._forward = OmsHub(
            symbol=symbol, interval=interval, strategy=self._strategy_factory(),
            cash=_FORWARD_CASH, fee_rate=_FORWARD_FEE, seed_bars=seed,
            store=self.store, on_step=None, created_ts=int(time.time() * 1000),
        )
        self._fwd_bars = []
        self._set_backtest_controls_enabled(False)
        self.btn_forward.setText("■ Stop forward")
        self._set_feed_health("live")

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
            ch.set_overlays(overlays)  # pipeline is Studio-only (no central chart)
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

    def _exec_max_leverage(self) -> float | None:
        """Maximum leverage cap for arming. Task-6 will read this from QSettings; for now return a
        conservative default (20×). The UI arm bar can override via ``_arm_max_leverage``."""
        return getattr(self, "_arm_max_leverage", 20.0)

    def _arm_float(self, env_key: str) -> float | None:
        """Read an optional float cap: first from the ``_arm_caps`` dict (set by the Task-5 UI arm
        bar), then from the environment variable *env_key*. Returns None when absent/blank so the
        matching RiskLimits field stays dormant (spot tests are byte-identical)."""
        import os
        raw = getattr(self, "_arm_caps", {}).get(env_key) if hasattr(self, "_arm_caps") else None
        raw = raw if raw is not None else os.environ.get(env_key)
        try:
            return float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None

    def _maybe_start_live_exec(self, spec: "ExecArmSpec | None" = None) -> bool:  # noqa: F821
        """Gate live exec on explicit flag + creds presence (GUI process only). Returns True when a
        LiveExecutionSession was built. VIKE_DISABLE_LIVE (headless) or blank flags/absent creds ->
        stay paper (the default) and start no worker.

        When *spec* is None (the default env-var path) the arm spec is resolved from environment
        variables (VIKE_EXEC_VENUE / VIKE_EXEC_ENV / …). When *spec* is supplied (Task-5 UI arm
        bar), its venue/environment/product/symbol/leverage are used directly — no env reads.
        """
        import os
        import time

        if os.environ.get("VIKE_DISABLE_LIVE"):
            return False
        from ..exec.arm_spec import resolve_arm_spec
        if spec is None:
            spec = resolve_arm_spec(venue=None, environment=None, product=None,
                                    symbol=self._symbol, leverage=None)
        if spec is None:
            return False
        venue = spec.venue
        env_name = spec.environment

        from ..exec.credentials import Environment
        from ..exec.venue_config import resolve_venue_config

        try:
            environment = Environment[env_name]
        except KeyError:
            return False
        cfg = resolve_venue_config(venue, environment, now_ms=lambda: int(time.time() * 1000))
        if cfg is None:
            return False

        from ..exec.accounting import Account
        from ..exec.binance.transport import get_public_json
        from ..exec.bus import EventBus
        from ..exec.live_oms import LiveOmsHub
        from ..exec.risk import RiskGate, RiskLimits
        from ..ui.private_user_data import LiveExecutionSession

        symbol = spec.symbol
        product = spec.product
        if venue == "bybit" and product == "perp":
            import logging
            from ..exec.bybit.perp_client import BybitPerpExecutionClient
            from ..exec.bybit.perp_instruments import parse_bybit_perp_instruments
            info = get_public_json(cfg.rest_base_url, "/v5/market/instruments-info",
                                   {"category": "linear", "symbol": symbol})
            if info.get("retCode", 0) != 0:
                logging.getLogger(__name__).error(
                    "Bybit linear instruments-info error retCode=%s — aborting live exec",
                    info.get("retCode"))
                return False
            parsed = parse_bybit_perp_instruments(info)
            if symbol not in parsed:
                logging.getLogger(__name__).error(
                    "Bybit linear instruments: symbol %r absent — aborting live exec", symbol)
                return False
            f = parsed[symbol]
            filters = {k: v for k, v in f.items() if k != "base_asset"}
            base_asset = f.get("base_asset", "")
            bus = EventBus()
            client_symbol = symbol
            from ..exec.risk import clamp_leverage
            leverage = clamp_leverage(spec.leverage, self._exec_max_leverage())
            client = BybitPerpExecutionClient(
                bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url, symbol=symbol,
                filters=filters, base_asset=base_asset, leverage=leverage)
            client.set_leverage()   # MAIN thread, before connect/reconcile
            # Perp MARKET orders are now valued at the seeded Account mark in the gate (slice 5f):
            # LiveOmsHub.submit_ticket reads account.marks for price-less orders. No false veto.
            # Correctness proven at the LiveOmsHub UNIT level (Task 3: test_live_oms_mark_seed.py);
            # the offscreen GUI arm tests prove session CONSTRUCTION only (no perp MARKET submitted).
        elif venue == "bybit":
            from ..exec.bybit.client import BybitSpotExecutionClient
            from ..exec.bybit.instruments import parse_bybit_instruments_info
            info = get_public_json(cfg.rest_base_url, "/v5/market/instruments-info",
                                   {"category": "spot", "symbol": symbol})
            if info.get("retCode", 0) != 0:
                import logging
                logging.getLogger(__name__).error(
                    "Bybit instruments-info error retCode=%s msg=%s — aborting live exec",
                    info.get("retCode"), info.get("retMsg"))
                return False
            parsed = parse_bybit_instruments_info(info)
            if symbol not in parsed:
                import logging
                logging.getLogger(__name__).error(
                    "Bybit instruments-info: symbol %r absent from response — aborting live exec",
                    symbol)
                return False
            f = parsed[symbol]
            filters = {k: v for k, v in f.items() if k != "base_asset"}
            base_asset = f.get("base_asset", "")
            bus = EventBus()
            client_symbol = symbol
            client = BybitSpotExecutionClient(
                bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url, symbol=symbol,
                filters=filters, base_asset=base_asset)
        elif venue == "okx" and product == "perp":
            import functools, logging
            from ..exec.okx.perp_client import OKXPerpExecutionClient
            from ..exec.okx.perp_instruments import parse_okx_perp_instruments
            from ..exec.okx.transport import okx_public_get, okx_signed_request
            from ..data.okx_source import market_symbol

            inst_id = f"{market_symbol(symbol)}-SWAP"      # BTCUSDT -> BTC-USDT -> BTC-USDT-SWAP
            simulated = environment is not Environment.MAINNET
            info = okx_public_get(cfg.rest_base_url, "/api/v5/public/instruments",
                                  {"instType": "SWAP", "instId": inst_id}, simulated=simulated)
            if str(info.get("code", "0")) != "0":
                logging.getLogger(__name__).error(
                    "OKX SWAP instruments error code=%s — aborting live exec", info.get("code"))
                return False
            parsed = parse_okx_perp_instruments(info)
            if inst_id not in parsed:
                logging.getLogger(__name__).error(
                    "OKX SWAP instruments: instId %r absent — aborting live exec", inst_id)
                return False
            f = parsed[inst_id]
            ct_val = f.get("ct_val", 0.0)
            if ct_val <= 0.0:                              # #1 trap: no ctVal -> 100x risk; abort
                logging.getLogger(__name__).error(
                    "OKX SWAP instruments: ctVal missing/zero for %r — aborting live exec", inst_id)
                return False
            filters = {k: v for k, v in f.items() if k not in ("base_asset", "ct_val", "ct_mult")}
            base_asset = f.get("base_asset", "")
            bus = EventBus()
            client_symbol = inst_id                        # BTC-USDT-SWAP — hub matches client/snapshot key
            from ..exec.risk import clamp_leverage
            leverage = clamp_leverage(spec.leverage, self._exec_max_leverage())
            client = OKXPerpExecutionClient(
                bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url, symbol=inst_id,
                filters=filters, base_asset=base_asset, ct_val=ct_val, leverage=leverage,
                transport=functools.partial(okx_signed_request, simulated=simulated),
                public_transport=functools.partial(okx_public_get, simulated=simulated))
            client.set_leverage()                          # MAIN thread, before connect/reconcile
            # Perp MARKET orders are now valued at the seeded Account mark in the gate (slice 5f):
            # LiveOmsHub.submit_ticket reads account.marks for price-less orders. No false veto.
            # Correctness proven at the LiveOmsHub UNIT level (Task 3: test_live_oms_mark_seed.py);
            # the offscreen GUI arm tests prove session CONSTRUCTION only (no perp MARKET submitted).
        elif venue == "okx":
            import functools
            from ..exec.okx.client import OKXSpotExecutionClient
            from ..exec.okx.instruments import parse_okx_instruments
            from ..exec.okx.transport import okx_public_get, okx_signed_request
            from ..data.okx_source import market_symbol

            inst_id = market_symbol(symbol)                 # BTCUSDT -> BTC-USDT
            simulated = environment is not Environment.MAINNET   # DEMO -> x-simulated-trading:1
            info = okx_public_get(cfg.rest_base_url, "/api/v5/public/instruments",
                                  {"instType": "SPOT", "instId": inst_id}, simulated=simulated)
            if str(info.get("code", "0")) != "0":
                import logging
                logging.getLogger(__name__).error(
                    "OKX instruments error code=%s msg=%s — aborting live exec",
                    info.get("code"), info.get("msg"))
                return False
            parsed = parse_okx_instruments(info)
            if inst_id not in parsed:
                import logging
                logging.getLogger(__name__).error(
                    "OKX instruments: instId %r absent from response — aborting live exec", inst_id)
                return False
            f = parsed[inst_id]
            filters = {k: v for k, v in f.items() if k != "base_asset"}
            base_asset = f.get("base_asset", "")
            bus = EventBus()
            client_symbol = inst_id          # BTC-USDT; hub must match the client/snapshot key
            client = OKXSpotExecutionClient(
                bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url, symbol=inst_id,
                filters=filters, base_asset=base_asset,
                transport=functools.partial(okx_signed_request, simulated=simulated),
                public_transport=functools.partial(okx_public_get, simulated=simulated))
        elif venue == "binance" and product == "perp":
            import logging
            from ..exec.binance.perp_client import BinancePerpExecutionClient
            from ..exec.binance.perp_instruments import parse_binance_perp_instruments
            from ..exec.venue_config import binance_fapi_rest
            fapi_rest = binance_fapi_rest(environment)
            info = get_public_json(fapi_rest, "/fapi/v1/exchangeInfo", {"symbol": symbol})
            parsed = parse_binance_perp_instruments(info)
            if symbol not in parsed:
                logging.getLogger(__name__).error(
                    "Binance fapi exchangeInfo: symbol %r absent — aborting live exec", symbol)
                return False
            f = parsed[symbol]
            filters = {k: v for k, v in f.items() if k != "base_asset"}
            base_asset = f.get("base_asset", "")
            bus = EventBus()
            client_symbol = symbol                              # plain BTCUSDT — no dash, no -SWAP
            from ..exec.risk import clamp_leverage
            leverage = clamp_leverage(spec.leverage, self._exec_max_leverage())
            client = BinancePerpExecutionClient(
                bus, signer=cfg.signer, rest_base_url=fapi_rest, symbol=symbol,
                filters=filters, base_asset=base_asset, leverage=leverage)
            client.set_leverage()                               # MAIN thread, before connect/reconcile
            # Perp MARKET orders are now valued at the seeded Account mark in the gate (slice 5f):
            # LiveOmsHub.submit_ticket reads account.marks for price-less orders. No false veto.
            # Correctness proven at the LiveOmsHub UNIT level (Task 3: test_live_oms_mark_seed.py);
            # the offscreen GUI arm tests prove session CONSTRUCTION only (no perp MARKET submitted).
        elif venue == "deribit":
            import logging
            from ..data.options.deribit import parse_instrument_name
            from ..exec.deribit.client import DeribitExecutionClient
            from ..exec.deribit.public import fetch_option_instruments
            from ..exec.deribit.transport import DeribitOrderTransport

            currency = (parse_instrument_name(symbol) or [None])[0]
            if currency is None:
                logging.getLogger(__name__).error(
                    "Deribit arm: symbol %r is not a valid option instrument name — aborting live exec",
                    symbol)
                return False
            all_instruments = fetch_option_instruments(currency, base_url=cfg.rest_base_url)
            if symbol not in all_instruments:
                logging.getLogger(__name__).error(
                    "Deribit arm: instrument %r not found in public/get_instruments — aborting live exec",
                    symbol)
                return False
            filters = all_instruments[symbol]
            bus = EventBus()
            client_symbol = symbol   # instrument_name IS the hub symbol — no market_symbol transform
            transport = DeribitOrderTransport(
                ws_url=cfg.ws_base_url,
                client_id=cfg.credentials.api_key,
                client_secret=cfg.credentials.api_secret,
                now_ms=lambda: int(time.time() * 1000),
            )
            transport.connect()   # MAIN thread: open + auth the order socket
            client = DeribitExecutionClient(
                bus, transport=transport, symbol=symbol, filters=filters,
                currency=currency)
        else:
            from ..data.instrument_db import parse_symbol_filters
            from ..exec.binance.client import BinanceSpotExecutionClient
            info = get_public_json(cfg.rest_base_url, "/api/v3/exchangeInfo", {"symbol": symbol})
            filters = parse_symbol_filters(info).get(symbol, {
                "tick_size": 0.01, "step_size": 0.001, "min_qty": 0.0, "max_qty": 0.0,
                "min_notional": 0.0})
            base_asset = next((s["baseAsset"] for s in info.get("symbols", [])
                               if s.get("symbol") == symbol), "")
            bus = EventBus()
            client_symbol = symbol
            client = BinanceSpotExecutionClient(
                bus, signer=cfg.signer, rest_base_url=cfg.rest_base_url, symbol=symbol,
                filters=filters, base_asset=base_asset)
        # OKX SWAP: filters["step_size"] is the lotSz in CONTRACTS, but the order qty fed to the gate
        # is in BASE units. Quantize the gate on the true BASE granularity (lotSz * ctVal); the client's
        # _to_contracts re-floors to lotSz afterward. Other venues' step_size is already in base.
        gate_lot_size = filters["step_size"]
        if venue == "okx" and product == "perp":
            gate_lot_size = (filters["step_size"] or 0.0) * ct_val
        _is_perp = (product == "perp")
        _max_order = self._arm_float("VIKE_EXEC_MAX_ORDER_NOTIONAL")
        _max_expo = self._arm_float("VIKE_EXEC_MAX_EXPOSURE")
        gate = RiskGate(RiskLimits(
            tick_size=filters["tick_size"] or None, lot_size=gate_lot_size or None,
            min_notional=filters["min_notional"] or None,
            max_notional_per_order=_max_order,
            max_total_exposure=_max_expo,
            block_reduce_only_overshoot=_is_perp))
        hub = LiveOmsHub(bus=bus, account=Account(), gate=gate, client=client,
                         venue=venue, symbol=client_symbol, now_ms=lambda: int(time.time() * 1000))
        hub.apply_snapshot(client.connect())   # reconcile on the MAIN thread before any fill
        self._exec_session = LiveExecutionSession(hub)
        if venue == "bybit" and product == "perp" and cfg.ws_base_url:
            from ..exec.bybit.perp_user_data import make_bybit_perp_run_core
            from ..ui.private_user_data import PrivateUserDataWorker
            run_core = make_bybit_perp_run_core(
                ws_url=cfg.ws_base_url,
                api_key=cfg.credentials.api_key,
                api_secret=cfg.credentials.api_secret,
                symbol=client_symbol,
                now_ms=lambda: int(time.time() * 1000),
            )
            worker = PrivateUserDataWorker(run_core)
            self._exec_session.add_worker_if_enabled("bybit", worker)
            from ..exec.bybit.funding import BybitFundingPoller
            bybit_funding_poller = BybitFundingPoller(bus=bus, client=client, symbol=client_symbol)
            self._funding_pollers.append(bybit_funding_poller)
            # NO opportunistic poll at arm time — that would issue a real signed REST GET on the main
            # thread the instant a session arms (incl. the offscreen GUI arm-tests that delete
            # VIKE_DISABLE_LIVE), violating the no-network-in-headless rule. The QTimer does the first
            # poll within _FUNDING_POLL_MS — negligible vs the ~8h funding cadence.
            if not os.environ.get("VIKE_DISABLE_LIVE"):
                self._funding_timer.start(_FUNDING_POLL_MS)
        elif venue == "bybit" and cfg.ws_base_url:
            from ..exec.bybit.user_data import make_bybit_run_core
            from ..ui.private_user_data import PrivateUserDataWorker
            run_core = make_bybit_run_core(
                ws_url=cfg.ws_base_url,
                api_key=cfg.credentials.api_key,
                api_secret=cfg.credentials.api_secret,
                symbol=client_symbol,
                now_ms=lambda: int(time.time() * 1000),
            )
            worker = PrivateUserDataWorker(run_core)
            self._exec_session.add_worker_if_enabled("bybit", worker)
        if venue == "okx" and product == "perp" and cfg.ws_base_url:
            from ..exec.okx.perp_user_data import make_okx_perp_run_core
            from ..ui.private_user_data import PrivateUserDataWorker
            run_core = make_okx_perp_run_core(
                ws_url=cfg.ws_base_url,
                api_key=cfg.credentials.api_key,
                api_secret=cfg.credentials.api_secret,
                passphrase=cfg.credentials.passphrase,
                symbol=client_symbol,                        # BTC-USDT-SWAP
                ct_val=ct_val,
                now_ms=lambda: int(time.time() * 1000),
            )
            worker = PrivateUserDataWorker(run_core)
            self._exec_session.add_worker_if_enabled("okx", worker)
            from ..exec.okx.funding import OkxFundingPoller
            okx_funding_poller = OkxFundingPoller(bus=bus, client=client, symbol=client_symbol)
            self._funding_pollers.append(okx_funding_poller)
            # NO opportunistic poll at arm time (see the bybit arm) — the QTimer does the first poll
            # within _FUNDING_POLL_MS, keeping the headless arm-tests network-free.
            if not os.environ.get("VIKE_DISABLE_LIVE"):
                self._funding_timer.start(_FUNDING_POLL_MS)   # ~ every 5 min; funding settles ~8h
        elif venue == "okx" and cfg.ws_base_url:
            from ..exec.okx.user_data import make_okx_run_core
            from ..ui.private_user_data import PrivateUserDataWorker
            run_core = make_okx_run_core(
                ws_url=cfg.ws_base_url,
                api_key=cfg.credentials.api_key,
                api_secret=cfg.credentials.api_secret,
                passphrase=cfg.credentials.passphrase,      # EXTRA arg the OKX arm needs
                symbol=client_symbol,                        # dashed inst_id 'BTC-USDT' — matches hub.symbol
                now_ms=lambda: int(time.time() * 1000),
            )
            worker = PrivateUserDataWorker(run_core)
            self._exec_session.add_worker_if_enabled("okx", worker)
        if venue == "binance" and product == "perp":
            from ..exec.venue_config import binance_fapi_rest, binance_fapi_ws
            _fapi_ws = binance_fapi_ws(environment)
            if _fapi_ws:
                from ..exec.binance.perp_user_data import make_binance_perp_run_core
                from ..ui.private_user_data import PrivateUserDataWorker
                run_core = make_binance_perp_run_core(
                    fapi_rest_url=binance_fapi_rest(environment),
                    ws_base_url=_fapi_ws,
                    api_key=cfg.credentials.api_key,
                    symbol=client_symbol,                        # plain BTCUSDT — no dash, no -SWAP
                    now_ms=lambda: int(time.time() * 1000),
                )
                worker = PrivateUserDataWorker(run_core)
                self._exec_session.add_worker_if_enabled("binance", worker)
        elif venue == "binance" and product != "perp" and cfg.ws_base_url:
            from ..exec.binance.user_data import make_binance_run_core
            from ..ui.private_user_data import PrivateUserDataWorker
            run_core = make_binance_run_core(
                ws_url=cfg.ws_base_url,
                api_key=cfg.credentials.api_key,
                api_secret=cfg.credentials.api_secret,
                symbol=client_symbol,                        # plain 'BTCUSDT' — no dashed inst_id, no passphrase
                now_ms=lambda: int(time.time() * 1000),
            )
            worker = PrivateUserDataWorker(run_core)
            self._exec_session.add_worker_if_enabled("binance", worker)
        if venue == "deribit" and cfg.ws_base_url:
            from ..exec.deribit.user_data import make_deribit_run_core
            from ..ui.private_user_data import PrivateUserDataWorker
            run_core = make_deribit_run_core(
                ws_url=cfg.ws_base_url,
                client_id=cfg.credentials.api_key,
                client_secret=cfg.credentials.api_secret,
                symbol=client_symbol,
                currency=currency,
                now_ms=lambda: int(time.time() * 1000),
            )
            worker = PrivateUserDataWorker(run_core)
            self._exec_session.add_worker_if_enabled("deribit", worker)
        return True

    # ------------------------------------------------------------------
    # Task 5 — ExecArmBar call sites
    # ------------------------------------------------------------------

    def _on_arm_requested(self, spec) -> bool:
        """Production call site: called when the user clicks Arm in ExecArmBar.

        If a session is already armed, ignores the request (Disarm first). Otherwise
        persists the non-secret selection, starts live exec and updates the feed badge.
        Returns True when a LiveExecutionSession was successfully built.
        """
        if getattr(self, "_exec_session", None) is not None:
            return True                      # already armed — ignore (Disarm first)
        self._persist_arm_selection(spec)    # QSettings (non-secret only)
        ok = self._maybe_start_live_exec(spec=spec)
        if ok:
            self._refresh_feed_badge_for_exec(spec)
            self.exec_arm.set_armed(True)    # flip the button to Disarm (teardown now user-reachable)
            self._armed_env = spec.environment   # single source of truth for _confirm_order
            sess = getattr(self, "_exec_session", None)
            if sess is not None and sess.hub is not None:
                self.order_ticket.set_armed(
                    True, venue=sess.hub.venue, symbol=sess.hub.symbol,
                    environment=spec.environment)
                sess.hub.bus.subscribe(self._on_exec_event)   # main-thread feedback subscriber
                from ..exec.positions_view import project_positions_orders
                self.positions_panel.set_armed(True)
                self.positions_panel.set_rows(project_positions_orders(   # seed once at arm
                    sess.hub.account, sess.hub.registry, sess.hub.venue))
        return ok

    def _on_disarm_requested(self) -> None:
        """Tear down the live-exec session cleanly.

        Stops the funding timer and clears the pollers list so _funding_tick no longer
        fires signed REST GETs against a detached client/bus. Re-arming after a disarm
        is safe: _maybe_start_live_exec restarts the timer on the next arm.
        """
        sess = getattr(self, "_exec_session", None)
        if sess is not None:
            if sess.hub is not None:
                try:
                    sess.hub.bus.unsubscribe(self._on_exec_event)   # detach feedback before teardown
                except Exception:
                    pass
            sess.shutdown()
            self._exec_session = None
        self._armed_env = ""              # clear single-source-of-truth env
        self._exec_symbol_override = None   # 6e: drop a consumed/stale option pick so the next
                                            # arm re-reads self._symbol (or a fresh pick)
        # Stop the funding timer and clear pollers so _funding_tick no longer fires
        # synchronous signed REST GETs against a now-detached client/bus (live network
        # after teardown).  Mirrors the cleanup done in shutdown() / _stop_all_timers().
        self._funding_timer.stop()
        self._funding_pollers = []
        self._update_feed_health()           # back to CACHED/LIVE data-feed badge
        self.exec_arm.set_armed(False)       # flip the button back to Arm + unlock the selectors
        self.order_ticket.set_armed(False)
        self.positions_panel.set_armed(False)   # clears rows + disables Cancel (no cancel when no session)

    def _refresh_feed_badge_for_exec(self, spec) -> None:
        """Update the status-bar feed badge to show the armed venue, product and leverage."""
        suffix = f" · PERP {int(spec.leverage)}x" if spec.product == "perp" else " · SPOT"
        warn = spec.environment == "MAINNET"
        self._feed_badge.setText(
            f"● {spec.venue.upper()}{suffix} · {spec.environment}"
        )
        # reuse theme.DOWN accent for MAINNET warning; theme.UP for DEMO
        # (do NOT edit theme.py — constraint from the project guide)
        self._feed_badge.setStyleSheet(
            f"color: {theme.DOWN if warn else theme.UP};"
            f"font-size:10px;background:transparent;border:none;"
            f"padding:3px 6px;margin-right:6px;"
        )

    def _on_submit_ticket(self, inputs: dict) -> None:
        """Production caller of LiveOmsHub.submit_ticket. Inert unless a session is armed (which cannot
        happen under VIKE_DISABLE_LIVE — _maybe_start_live_exec returns False there, so _exec_session
        stays None). Confirms before sending a real order; routes through the existing gate+bus."""
        import time

        sess = getattr(self, "_exec_session", None)
        if sess is None or sess.hub is None:
            return  # not armed / headless -> inert (no dialog, no network)
        hub = sess.hub
        from ..exec.order_ticket import build_order_request
        try:
            req = build_order_request(
                hub_venue=hub.venue, hub_symbol=hub.symbol,
                side=int(inputs["side"]), qty=float(inputs["qty"]),
                order_type=str(inputs["order_type"]),
                price=inputs.get("price"),
                reduce_only=bool(inputs.get("reduce_only", False)),
                client_order_id=self._coid.mint(), now_ms=int(time.time() * 1000),
            )
        except ValueError as exc:
            self.order_ticket.set_status(f"invalid: {exc}")
            return
        if not self._confirm_order(req):
            return
        self._ticket_status.arm(req.client_order_id)
        self.order_ticket.set_status("sending…")
        hub.submit_ticket(req)   # synchronous REST-ACK on the GUI thread (no new worker thread)

    def _confirm_order(self, req) -> bool:
        """Modal confirm — reachable ONLY from the live armed path (never under VIKE_DISABLE_LIVE).
        Shows venue/symbol/side/qty/type/price/ENV; no secrets. MAINNET gets a warning icon.

        Uses ``self._armed_env`` (stored in ``_on_arm_requested``) as the single source of truth for
        the environment — NOT ``exec_arm._env.currentText()``, which reflects the CURRENTLY SELECTED
        (not necessarily armed) environment and couples to ExecArmBar internals."""
        env_text = getattr(self, "_armed_env", "") or "DEMO"
        side = "BUY" if req.side > 0 else "SELL"
        px = "MKT" if req.price is None else f"{req.price:,.2f}"
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning
                    if env_text == "MAINNET" else QtWidgets.QMessageBox.Icon.Question)
        box.setWindowTitle("Confirm live order")
        box.setText(
            f"{env_text} · {req.venue.upper()} · {req.symbol}\n"
            f"{side} {req.qty} @ {px} ({req.order_type})"
            + ("  reduce-only" if req.reduce_only else ""))
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok
                               | QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        return box.exec() == QtWidgets.QMessageBox.StandardButton.Ok

    def _on_cancel_ticket(self, coid: str) -> None:
        """Per-order Cancel from the positions panel. Armed-only + confirm; inert headless.

        Mirrors _on_submit_ticket's inert-guard: _exec_session is None under VIKE_DISABLE_LIVE
        (_maybe_start_live_exec returns False), so this early-returns with NO dialog, NO network.
        cancel_ticket runs a synchronous signed REST DELETE on the GUI thread (same as submit_ticket);
        no worker thread (a one-shot call would re-introduce the 0xC0000409 join discipline).
        """
        sess = getattr(self, "_exec_session", None)
        if sess is None or sess.hub is None:
            return  # not armed / headless -> inert
        if not self._confirm_cancel(coid):
            return
        try:
            sess.hub.cancel_ticket(coid)
        except Exception as exc:  # noqa: BLE001 - a venue error (auth/rate-limit) must not crash the slot
            import logging
            logging.getLogger(__name__).warning("cancel failed for %s: %s", coid, exc)

    def _confirm_cancel(self, coid: str) -> bool:
        """Light Question modal (cancel REMOVES risk, so no MAINNET-warning escalation). Reachable
        ONLY from the armed path -> never under VIKE_DISABLE_LIVE / offscreen (no headless hang).
        Shows env/venue/symbol + the order's side/qty/type/price from the registry; no secrets."""
        sess = getattr(self, "_exec_session", None)
        if sess is None or sess.hub is None:
            return False
        env_text = getattr(self, "_armed_env", "") or "DEMO"
        mo = sess.hub.registry.get(coid)
        if mo is None:
            return False
        req = mo.request
        side = "BUY" if req.side > 0 else "SELL"
        px = "MKT" if req.price is None else f"{req.price:,.2f}"
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        box.setWindowTitle("Cancel order")
        box.setText(f"{env_text} · {req.venue.upper()} · {req.symbol}\n"
                    f"Cancel {side} {req.qty} @ {px} ({req.order_type})?")
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok
                               | QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        return box.exec() == QtWidgets.QMessageBox.StandardButton.Ok

    def _on_exec_event(self, event) -> None:
        """Main-thread bus subscriber driving the ticket's status + position lines. Read-and-render
        ONLY — never bus.publish (the bus defers nested publishes; re-publishing here would enqueue
        into the live drain). Inert if the ticket/session has gone (late event during teardown)."""
        if getattr(self, "_closing", False):
            return
        sess = getattr(self, "_exec_session", None)
        if sess is None or sess.hub is None:
            return
        text = self._ticket_status.on_event(event)
        if text:
            self.order_ticket.set_status(text)
        # refresh the one-line position read (BOTH leg — correct for spot + one-way perp)
        hub = sess.hub
        pos = hub.account.positions.get((hub.venue, hub.symbol, "BOTH"))
        if pos is not None:
            upnl = hub.account.unrealized_pnl(hub.venue, hub.symbol)
            self.order_ticket.set_position(
                f"pos: {pos['size']} @ {pos['avg_px']:.2f}  uPnL {upnl:.2f}")
        # Live Positions & Open-Orders panel: re-project the armed hub's read-model. Cheap dict
        # iteration; the cost is the QTableWidget rebuild (fine at human order rates — no throttle).
        from ..exec.positions_view import project_positions_orders
        self.positions_panel.set_rows(
            project_positions_orders(hub.account, hub.registry, hub.venue))

    def _persist_arm_selection(self, spec) -> None:
        """Write the non-secret exec selector state to QSettings.

        Saved: venue / product / environment / leverage.
        NEVER written: api_key / secret / passphrase — creds live in .env via load_credentials.
        """
        from PySide6 import QtCore
        s = QtCore.QSettings("vike", "trader")
        s.setValue("exec/venue", spec.venue)
        s.setValue("exec/product", spec.product)
        s.setValue("exec/environment", spec.environment)
        s.setValue("exec/leverage", spec.leverage)

    def _restore_arm_selection(self) -> None:
        """Restore saved exec selector state into exec_arm combos.

        Restores the SELECTION only — never calls _on_arm_requested (no auto-arm at launch).
        """
        from PySide6 import QtCore
        s = QtCore.QSettings("vike", "trader")
        v = s.value("exec/venue")
        if not v:
            return
        product_raw = str(s.value("exec/product", "Spot"))
        # Normalize to title-case for the combo ("spot"/"perp" -> "Spot"/"Perp")
        product = product_raw.capitalize()
        self.exec_arm.set_selection(
            venue=str(v),
            product=product,
            environment=str(s.value("exec/environment", "DEMO")),
            leverage=int(float(s.value("exec/leverage", 1))),
        )
        # restore selection ONLY — never call _on_arm_requested here (no auto-arm at launch).

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        self._apply_titlebar_color()  # native caption colour needs a live HWND (post-show)
        self._place_rules()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._place_rules()
        self._fit_chart_header()   # chart width changed -> re-pin the header's ⧉ ─ □ ✕
        self._reflow_frames()      # keep MAXIMIZED windows filling the workspace as it changes
        # NB: floating (non-maximized) windows are intentionally NOT re-tiled on resize — the user
        # owns their layout; only Window>Arrange re-tiles.

    def _reflow_frames(self) -> None:
        """On a workspace resize, re-fit every attached chartwin frame via host_resized(): a
        MAXIMIZED window must keep filling the workspace (else a chart maximized at the old size
        leaves empty space when the main window grows / OS-maximizes — the reported bug), and a
        floating one is clamped back in-bounds. host_resized() existed for exactly this but was
        never wired to MainWindow.resizeEvent."""
        if self._closing:
            return
        for f in (self._chart_frames + list(self._tool_frames.values())
                  + list(self._panel_frames.values())):
            try:
                f.host_resized()
            except RuntimeError:
                pass

    def _place_rules(self) -> None:
        """Size the separator overlay to the whole window and tell it where the bottom rule
        goes (the top of the status bar). The overlay paints both lines in device pixels."""
        overlay = getattr(self, "_rules", None)
        if overlay is None:
            return
        overlay.setGeometry(0, 0, self.width(), self.height())
        sb = self.statusBar()
        bottom_y = sb.geometry().top() if sb is not None else self.height() - 1
        overlay.set_lines(bottom_y)
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
            # Studio chart only exists while its dock is open; capture its indicators when present.
            studio_indicators=(indicator_states(self.studio_price)
                               if self.studio_price is not None else []),
            documents=[self._doc_state_with_geometry(d) for d in self._doc_widgets],
            open_tools=list(self._tool_docks.keys()),          # tools open as docks
            tool_windows=self._tool_window_states(),           # Stage A3: tools open as windows
            watchlist_link=self._watchlist_link,
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
        self._close_all_chart_docks()           # close any docked charts (unregister their docs)
        self._close_all_tool_windows()          # dispose torn-out tool windows (with teardown)
        self._close_all_panel_windows()         # re-home any floated side panels (Market/Trades)

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
        try:
            self._watchlist_dot.set_group(self._watchlist_link)
        except RuntimeError:
            # _watchlist_dot can be a stale/deleted LinkDot here (e.g. the watchlist panel was torn
            # down) — _watchlist_link is still recorded, so skipping the repaint is safe (the
            # workspace still applies and a rebuilt dot reads the stored group).
            pass

        if state.dock_state_hex:                 # a saved layout; built-ins use default positions
            self._syncing_docks = True
            try:
                self.dock_manager.restoreState(
                    QtCore.QByteArray.fromHex(state.dock_state_hex.encode("ascii")))
            except Exception:  # noqa: BLE001 - stale/garbled blob -> keep the rebuilt default
                pass
            finally:
                self._syncing_docks = False
            self._reclaim_floating_docks()   # un-float any dock the workspace blob restored as a native float
        self.tabs.hide_space_tabs()   # restoreState re-shows space tabs; the rail switches spaces

        # Stage A3: recreate the workspace's torn-out tool windows (chartwin frames live outside
        # the dock blob, so after restoreState — same as the launch path).
        for spec in (getattr(state, "tool_windows", None) or []):
            if not isinstance(spec, dict):
                continue
            try:
                self._open_tool_window(spec.get("key"), spec.get("geometry"))
            except Exception:  # noqa: BLE001 - one bad/stale tool key must not break the load
                pass

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
        for _glyph, name, space_index in self._SPACE_ITEMS:   # Chart -> show/switch space
            cmds.append((f"Go to {name}", lambda idx=space_index: self.tabs.show_space(idx)))
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
                       "indicators": indicator_states(self.price) if self.price is not None else []}
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
        if chart is None:                      # no chart open -> nothing to export
            return
        path, _f = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export chart image", f"{self._symbol}.png", "PNG image (*.png)")
        if path:
            chart.grab().save(path)
            self.statusBar().showMessage(f"Saved {path}", 4000)

    def _show_shortcuts(self) -> None:
        rows = ["Ctrl+K — command palette", "Ctrl+N — new chart window",
                "/ — focus the command bar", "Ctrl+Shift+C / V — copy / paste window",
                "Ctrl+M / Ctrl+T — market watch / trades panels",
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
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        """ONE deterministic, idempotent teardown — called by closeEvent AND the test harness. Runs
        in STRICT order so no signal / timer / callback can touch a freed dock, and ADS tears its C++
        graph down only once everything is quiescent (the source of the CDockWidget-already-deleted +
        heap-corruption races). See docs/research/2026-06-16-deterministic-teardown-plan.md."""
        if getattr(self, "_closing", False):
            return
        self._closing = True   # every dock-touching slot now early-returns (the gates above)
        # 1. Reclaim native floats BEFORE save, so saveState() can't serialize a CFloatingDockContainer
        #    into the next launch's blob (which would resurrect it and re-open the C++ destructor race).
        self._drain_floats()
        self._save_session()   # snapshot (touches no dock now that floats are reclaimed)
        # 2. Disconnect the re-entrant ADS / watchlist signals so the close sweep can't trigger a
        #    currentChanged -> _on_tab_changed -> toggleView relayout into a half-freed dock.
        self._disconnect_teardown_signals()
        # 3. Stop EVERY timer (closeEvent used to miss _rollup/_timer/_clock/_retile).
        self._stop_all_timers()
        # 4. Join EVERY worker / feed.
        self._stop_forward()
        if getattr(self, "_live_hub", None) is not None:
            self._live_hub.shutdown()            # chart-document live round-robin + its worker
        if getattr(self, "_exec_session", None) is not None:
            sess = self._exec_session
            if getattr(sess, "hub", None) is not None:
                try:
                    sess.hub.bus.unsubscribe(self._on_exec_event)
                except Exception:
                    pass
            sess.shutdown()        # live exec workers + LiveOmsHub detach (Phase 3b)
            self._exec_session = None
        self._funding_pollers = []               # 5e: drop the REST funding pollers (no thread to join)
        self._stop_live_updates()
        if getattr(self, "_live_worker", None) is not None:
            self._live_worker.wait(2000)
            self._live_worker = None
        for w in list(getattr(self, "_layout_workers", [])):
            w.wait(5000)                         # in-flight layout-agent API call
        self._layout_workers = []
        if getattr(self, "news", None) is not None:
            self.news.stop_feed()
        if self.studio is not None:
            self.studio.shutdown()               # wait out the AI worker (if Studio open)
        if getattr(self, "_options_svc", None) is not None:
            self._options_svc.shutdown()
        # 5. Close EVERY frame + dock (safe now: re-entrant signals disconnected + _closing gates the
        #    rest, so each close handler only drops refs / runs its tool teardown — no ADS re-entry).
        self._close_all_frames_and_docks()
        # 6. Drain any float that materialized during the sweep, then tear the manager down while
        #    quiescent so ADS's C++ destructors run with nothing Python-side able to touch a dock.
        self._drain_floats()
        mgr = getattr(self, "dock_manager", None)
        if mgr is not None:
            try:
                mgr.deleteLater()
            except RuntimeError:
                pass
            QtWidgets.QApplication.processEvents()

    def _stop_all_timers(self) -> None:
        """Stop every MainWindow QTimer so no tick fires during/after teardown. (The live/forward/hub
        timers are stopped by _stop_live_updates / _stop_forward / _live_hub.shutdown in shutdown.)"""
        for name in ("_rollup_timer", "_timer", "_clock",
                     "_price_timer", "_refresh_timer", "_funding_timer"):
            t = getattr(self, name, None)
            if t is not None:
                try:
                    t.stop()
                except RuntimeError:
                    pass

    def _drain_floats(self) -> None:
        """Re-home + dispose every native ADS floating container synchronously, so none survives for
        ADS's racing C++ destructor at manager teardown. Bounded (ADS can materialize a restored float
        on a deferred tick); _reclaim_floating_docks re-homes, processEvents drains the deleteLater."""
        mgr = getattr(self, "dock_manager", None)
        if mgr is None:
            return
        for _ in range(5):
            try:
                if not list(mgr.floatingWidgets()):
                    return
            except (RuntimeError, AttributeError):
                return
            try:
                self._reclaim_floating_docks()
            except (RuntimeError, AttributeError):
                pass
            QtWidgets.QApplication.processEvents()

    def _disconnect_teardown_signals(self) -> None:
        """Disconnect the ADS-fired / watchlist signals whose slots re-enter ADS or relayout (the
        close sweep would otherwise fire them on a half-freed graph). The ref-drop slots
        (documentClosed -> _on_document_closed, frame.closed) stay connected — the sweep NEEDS them to
        unregister + deleteLater. Each disconnect fully guarded (the signal may already be gone)."""
        def _dc(sig):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        tabs = getattr(self, "tabs", None)
        if tabs is not None:
            try:
                _dc(tabs.currentChanged)     # -> _on_tab_changed + _on_space_changed (toggleView re-entry)
            except (RuntimeError, AttributeError):
                pass
            if hasattr(tabs, "detach"):
                try:
                    tabs.detach()            # SpaceDeck: drop area.currentChanged + suppress reconnect
                except (RuntimeError, AttributeError):
                    pass
        wl = getattr(self, "watchlist", None)
        if wl is not None:
            try:
                _dc(wl.symbolChosen)         # -> _load_symbol / _broadcast / open_in_new_chart
            except (RuntimeError, AttributeError):
                pass
        for dock in list(getattr(self, "_panel_dock_map", {}).values()):
            try:
                _dc(dock.viewToggled)        # -> _on_dock_view_toggled
            except (RuntimeError, AttributeError):
                pass

    def _tick_clock(self):
        if self._closing:
            return
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
    # Dark PALETTE to match the dark stylesheet. On resize, Qt fills a widget's newly-exposed region
    # with the palette Window/Base brush BEFORE the (deferred) stylesheet/pyqtgraph paintEvent runs —
    # with the default palette that flashed BLACK while dragging a window/chart edge (the heavy candle
    # view can't repaint every resize frame). A dark palette makes that transient the theme dark.
    _pal = app.palette()
    for _role in (QtGui.QPalette.Window, QtGui.QPalette.Base, QtGui.QPalette.Button):
        _pal.setColor(_role, QtGui.QColor(theme.BG))
    for _role in (QtGui.QPalette.WindowText, QtGui.QPalette.Text, QtGui.QPalette.ButtonText):
        _pal.setColor(_role, QtGui.QColor(theme.TEXT))
    app.setPalette(_pal)
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
    exit_code = app.exec()
    # PySide6 + PySide6-QtAds tear down a large C++ object graph (dock manager, floating + auto-hide
    # containers, charts) during the interpreter's final garbage collection — which intermittently
    # faults ("Fatal Python error: Aborted" while garbage-collecting / 0xC0000409). Everything
    # durable is already flushed in closeEvent (session) and by the logging handlers, so flush the
    # log and hand the OS the exit code directly, skipping the crash-prone final GC.
    import os as _os
    logging.shutdown()
    _os._exit(exit_code)


if __name__ == "__main__":
    main()
