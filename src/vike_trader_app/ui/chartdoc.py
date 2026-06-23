"""Multi-instance chart documents (Phase 2 of the workspace program).

A ``ChartDocument`` is a self-contained chart surface with its OWN symbol + interval —
unlike the Chart space, whose symbol is app-level state. N of them tab into the ADS center
area next to the spaces, tear out to floating windows (multi-monitor), and serialize into
the session/workspace manifest.

``LiveHub`` keeps every registered document's live edge ticking with ONE in-flight fetch
worker, round-robin across visible documents. The fetch itself is network-only (thread-safe
off the UI thread); merges land back on the main thread. Cache/Parquet reads (the
``dataload`` path) stay strictly on the main thread per the data-layer constraint.
"""

from __future__ import annotations

import os
import time

from PySide6 import QtCore, QtWidgets

from ..data.binance_source import interval_ms
from ..data.live_update import live_fetch_window, merge_live_bars
from ..data.sources import select_source
from . import theme
from .dataload import FRESH_MS, load_symbol_bars, lookback_start
from .session import apply_indicator_states, indicator_states
from .watchlist_data import is_stale

_LIVE_LOOKBACK = 5          # bars (incl. forming candle) pulled per live tick
_HUB_TICK_MS = 5_000        # round-robin cadence: each tick serves ONE visible document


def _set_topmost(window_id: int, on: bool) -> bool:
    """Pin/unpin a native window above all others (Win32 SetWindowPos TOPMOST; False where
    unsupported). Deliberately NOT Qt window flags: changing WindowStaysOnTopHint at either
    the QWidget or the QWindow level re-creates the native window, which corrupts the ADS
    floating container's state (it can close a DeleteOnClose chart document) and the new
    native window comes back with default flags anyway — verified empirically."""
    import ctypes
    import sys

    if sys.platform != "win32":
        return False
    from ctypes import wintypes

    # argtypes are REQUIRED: without them ctypes marshals the -1/-2 sentinel as a 32-bit
    # c_int where a 64-bit HWND is expected and SetWindowPos fails (returns 0) — verified.
    set_window_pos = ctypes.windll.user32.SetWindowPos
    set_window_pos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                               ctypes.c_int, ctypes.c_int, wintypes.UINT]
    set_window_pos.restype = wintypes.BOOL
    hwnd_topmost = wintypes.HWND(-1 if on else -2)   # HWND_TOPMOST / HWND_NOTOPMOST
    swp = 0x0001 | 0x0002 | 0x0010                   # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
    return bool(set_window_pos(wintypes.HWND(int(window_id)), hwnd_topmost, 0, 0, 0, 0, swp))


def make_chart_card(chart) -> QtWidgets.QWidget:
    """Build the rounded 'card' that frames a PriceChart: a transparent viewport over a CHART_BG
    card whose rounded corners show through (anti-aliased), with a vertical splitter hosting the
    oscillator panes below the price chart. Shared by the Chart space (MainWindow._build_central)
    AND every ChartDocument so the chart frame is constructed ONE way (chart unification)."""
    chart.setBackground(None)
    vp = chart.viewport()
    vp.setAutoFillBackground(False)
    vp.setStyleSheet("background:transparent;")
    chart.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
    card = QtWidgets.QWidget()
    card.setObjectName("chartCard")
    card.setStyleSheet(
        f"#chartCard{{background:{theme.CHART_BG};border:1px solid {theme.BORDER};"
        f"border-radius:16px;}}"
    )
    card_lay = QtWidgets.QVBoxLayout(card)
    card_lay.setContentsMargins(0, 0, 0, 0)
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.setHandleWidth(6)
    split.addWidget(chart)
    split.setStretchFactor(0, 1)
    chart.set_pane_host(split)
    card_lay.addWidget(split)
    return card


class ChartDocument(QtWidgets.QWidget):
    """A clean, standalone chart viewer document (PriceChart + oscillator pane host)."""

    symbolChanged = QtCore.Signal(str, str)   # (symbol, interval) — feeds tab title + link groups
    loadFinished = QtCore.Signal(int, bool)   # (load_gen, ok) — async network top-up landed/failed

    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h", parent=None):
        super().__init__(parent)
        from .chart import PriceChart  # heavy import kept local (pyqtgraph)

        self._symbol = symbol
        self._interval = interval
        self._bars: list = []
        self._loaded = False         # becomes True after the first real load attempt
        # Async load (off-thread gap top-up via LiveHub): _hub is set by LiveHub.register; _load_gen
        # is bumped per load() so a late worker result for a since-superseded symbol is discarded;
        # _topup_prev holds the rollback identity for a bad symbol; _topup_pending gates re-kicks.
        self._hub = None
        self._load_gen = 0
        self._topup_prev: tuple | None = None
        self._topup_pending = False

        self.chart = PriceChart(title_controls=True)  # interval/ƒx/style/range live in the title bar
        # same rounded-card treatment as the Chart space — built by the shared make_chart_card.
        card = make_chart_card(self.chart)
        outer = QtWidgets.QVBoxLayout(self)
        # Small TOP margin: the doc always lives in a ChartWindowFrame whose title bar sits directly
        # above, so the old 14px top left a dead gap between the window title and the chart toolbar.
        # Keep the side/bottom padding for the rounded card; just a 3px strip up top.
        outer.setContentsMargins(14, 3, 14, 14)
        outer.setSpacing(6)

        # Link groups (Phase 3): two colour dots in a thin header — an independent SYMBOL channel
        # (●) and INTERVAL/timeframe channel (◆), MultiCharts-style. Same symbol colour on >1 chart
        # syncs their symbol; same interval colour syncs their timeframe; the two are independent
        # (a chart can follow another's timeframe without following its symbol). 0 = unlinked. The
        # bus is injected via set_bus.
        from .panels import LinkDot  # local import keeps the panels<->chartdoc edge one-way

        self.link_group = 0
        self.interval_link_group = None      # None = follow the symbol link (back-compat default)
        self._bus = None
        # The link dots are created here but ADOPTED into the floating window's title bar by
        # chartwin.ChartWindowFrame (MC puts link colours on the window chrome). Chart documents
        # currently only ever live in a floating window, so they're not added to the chart toolbar.
        self._link_dot = LinkDot(0, label="Symbol")
        self._link_dot.groupChanged.connect(self._set_link_group)
        self._ivl_dot = LinkDot(-1, label="Interval", glyph=("◇", "◆"), follow=True)
        self._ivl_dot.groupChanged.connect(self._set_interval_link_group)
        # Keep-on-top pin (MultiCharts "stick window"): float-only chrome. NOT in a layout here —
        # the chart-window TITLE BAR adopts it (chartwin.ChartWindowFrame), MC's placement.
        self._pin_btn = QtWidgets.QToolButton()
        self._pin_btn.setCheckable(True)
        self._pin_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._pin_btn.setFixedSize(22, 22)
        self._pin_btn.setText("⊼")
        self._pin_btn.setToolTip("Keep this floating window on top")
        self._pin_btn.setStyleSheet(
            f"QToolButton{{border:none;background:transparent;color:{theme.TEXT3};"
            f"font-size:14px;}}"
            f"QToolButton:checked{{color:{theme.ACCENT};}}"
        )
        self._pin_btn.setVisible(False)
        self._pin_btn.toggled.connect(self._toggle_on_top)
        outer.addWidget(card)

        self.chart.intervalChosen.connect(lambda iv: self.load(interval=iv))
        # any symbol/interval change (user TF pick, link receive, programmatic load) re-broadcasts
        self.symbolChanged.connect(self._broadcast_link)

    # --- identity ---------------------------------------------------------------------------

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def interval(self) -> str:
        return self._interval

    def title(self) -> str:
        return f"{self._symbol} · {self._interval}"

    # --- data -------------------------------------------------------------------------------

    def load(self, symbol: str | None = None, interval: str | None = None, *,
             network: bool = True) -> bool:
        """(Re)load this document's series. NON-BLOCKING when ``network=True``.

        Phase 1 (MAIN THREAD): a cache-only read paints instantly — the Parquet/Catalog read is
        the thread-unsafe path, so it stays here, and it's fast (partition-pruned tail). Phase 2:
        if the cached tail is stale/cold AND ``network``, the gap fetch runs OFF-THREAD via the
        LiveHub worker and merges back on the main thread (``apply_topup``) — so a cold-symbol
        switch no longer freezes the UI on the inline REST pagination. ``network=False`` is a pure
        cache read (restore path). Returns whether cache bars were painted synchronously."""
        now = int(time.time() * 1000)
        prev_symbol, prev_interval = self._symbol, self._interval
        self._symbol = (symbol or self._symbol).upper()
        self._interval = interval or self._interval
        self._load_gen += 1  # supersede any in-flight top-up for the old symbol

        # Phase 1 — cache-only paint (main thread, fast). Never blocks on the network.
        res = load_symbol_bars(self._symbol, self._interval, now, network=False)
        switched = self._symbol != prev_symbol or self._interval != prev_interval
        if res.bars:
            self._bars = res.bars
            self._paint()
        elif switched:
            # Switched to a symbol/interval with NO cache: clear the stale view so the old series
            # isn't shown under the new label. The async top-up fills it (apply_topup), or a failed
            # fetch rolls back to the previous symbol (topup_failed).
            self._bars = []
            self._paint()
        self.symbolChanged.emit(self._symbol, self._interval)

        if not network:
            return bool(res.bars)
        # Fresh cached tail -> done, zero network (the common path).
        if res.bars and not is_stale(res.bars[-1].ts, now, FRESH_MS):
            self._loaded = True
            return True
        # Phase 2 — stale or cold: top up the gap OFF-THREAD via the hub (no UI freeze). The hub's
        # worker is network-only + waited on by shutdown(); the merge/persist land on the main
        # thread in apply_topup. No hub (bare doc, e.g. a unit test) -> stay cache-only.
        if self._hub is not None:
            self._topup_prev = (prev_symbol, prev_interval)
            self._topup_pending = True
            self._hub.request_topup(self, self._load_gen)
        return bool(res.bars)

    def _paint(self) -> None:
        """Push the current bars to the chart (shared by load + apply_topup)."""
        self.chart.set_data(self._bars, [])
        self.chart.set_overlays({})
        self.chart.set_title(self._symbol)
        self.chart.set_timeframe(self._interval)
        if self._bars:
            self.chart.show_upto(len(self._bars) - 1)

    def apply_topup(self, gen: int, bars: list) -> None:
        """MAIN THREAD: merge the off-thread gap fetch in, persist, repaint. A generation guard
        drops a result the user already superseded (switched symbol/interval mid-fetch)."""
        if gen != self._load_gen:
            return  # superseded — discard
        self._topup_pending = False
        if not bars:
            if self._bars:
                self._loaded = True  # cache was already showing; nothing new to merge
            self.loadFinished.emit(gen, bool(self._bars))
            return
        from ..data.cache import DEFAULT_ROOT, append_series, merge_bars
        try:
            append_series(bars, DEFAULT_ROOT, self._symbol, self._interval)  # persist (main thread)
        except Exception:  # noqa: BLE001 - a persist failure must not lose the in-memory merge
            pass
        self._bars = merge_bars(self._bars, bars) if self._bars else list(bars)
        self._loaded = True
        self._paint()
        self.loadFinished.emit(gen, True)

    def topup_failed(self, gen: int, _msg: str) -> None:
        """MAIN THREAD: the off-thread fetch failed. If this load produced NO bars at all (a bad
        symbol typed into the box), roll back to the previous symbol's cached view; otherwise keep
        the stale cache (a transient network error — ensure_loaded retries on next focus)."""
        if gen != self._load_gen:
            return
        self._topup_pending = False
        if not self._bars and self._topup_prev is not None:
            self._symbol, self._interval = self._topup_prev
            res = load_symbol_bars(self._symbol, self._interval, int(time.time() * 1000),
                                   network=False)
            if res.bars:
                self._bars = res.bars
                self._paint()
            self.symbolChanged.emit(self._symbol, self._interval)
        self.loadFinished.emit(gen, False)

    def ensure_loaded(self) -> None:
        """Top up over the network the first time the document is actually shown — restored
        background documents load cache-only at startup (no network storm).

        This is a focus-triggered top-up of THIS doc, not a user symbol change, so it must NOT
        broadcast to link peers: a restored doc carries the saved link group by now, and without
        the guard, simply focusing it would overwrite same-group peers with its stale symbol.

        Guarded on _topup_pending too: the network top-up is now async, so a second focus while
        the first fetch is still in flight must NOT kick a duplicate (it lands via apply_topup)."""
        if not self._loaded and not self._topup_pending:
            self._suppress_broadcast = True
            try:
                self.load()
            finally:
                self._suppress_broadcast = False

    def merge_live(self, fetched: list) -> None:
        """Main thread: merge live-edge bars from the hub and repaint if anything changed."""
        if not self._bars or not fetched:
            return
        merged, appended, replaced_last = merge_live_bars(self._bars, fetched)
        if appended or replaced_last:
            self._bars = merged
            self.chart.apply_live(merged, None, repaint=False)
            self.chart.show_upto(len(merged) - 1)

    # --- symbol link group ------------------------------------------------------------------

    def set_bus(self, bus) -> None:
        """Join a SymbolLinkBus as a receiver member (called by MainWindow on creation)."""
        self._bus = bus
        if bus is not None:
            bus.add_member(self)

    def _set_link_group(self, gid: int) -> None:
        self.link_group = gid
        self._link_dot.set_group(gid)   # keep the dot in sync when set directly (not via menu)

    def _set_interval_link_group(self, gid: int) -> None:
        # dot sentinel -1 ("follow symbol") maps to None on the bus; 0 = unlinked; 1-6 = own colour
        self.interval_link_group = None if gid < 0 else gid
        self._ivl_dot.set_group(gid)

    # --- floating-window chrome (keep-on-top pin) ---------------------------------------------

    def set_floating(self, floating: bool) -> None:
        """Dock tear-out notification (SpaceDeck forwards ``topLevelChanged``): the keep-on-top
        pin is float-only chrome — a docked tab can't sit above other windows."""
        self._pin_btn.setVisible(floating)
        if not floating:
            self._pin_btn.setChecked(False)   # the floating container is gone; reset the state

    def _toggle_on_top(self, on: bool) -> None:
        """Pin/unpin this document's DETACHED window above all others (MultiCharts 'stick').
        Native z-order only — see ``_set_topmost`` for why Qt window flags are off-limits.
        The window must be a real top-level (an S7 detached ChartWindowFrame — or any future
        float), not the main window itself."""
        w = self.window()
        if w is None or not w.isWindow() or isinstance(w, QtWidgets.QMainWindow):
            if on:                            # attached (or already re-attached) -> refuse
                self._pin_btn.setChecked(False)
            return
        _set_topmost(int(w.winId()), on)

    def _broadcast_link(self, symbol: str, interval: str) -> None:
        """Push this doc's symbol to its symbol-link group and interval to its (independent)
        interval-link group. The bus re-entrancy guard keeps a received apply_link -> load ->
        symbolChanged from looping back; _suppress_broadcast additionally blocks focus-triggered
        ensure_loaded top-ups from broadcasting."""
        if self._bus is not None and not getattr(self, "_suppress_broadcast", False):
            self._bus.broadcast(self.link_group, self, symbol, interval,
                                interval_group=self.interval_link_group)

    def apply_link(self, symbol: str | None, interval: str | None) -> None:
        """Receive a link broadcast: switch to (symbol, interval), keeping whichever is None."""
        self.load(symbol=symbol or self._symbol, interval=interval or self._interval)

    # --- persistence ------------------------------------------------------------------------

    def state(self) -> dict:
        return {"symbol": self._symbol, "interval": self._interval,
                "link_group": self.link_group,
                "interval_link_group": self.interval_link_group,
                "indicators": indicator_states(self.chart)}

    def apply_state(self, state: dict) -> None:
        """Re-attach saved indicators + link colours (call after a load put bars on the chart)."""
        from .linkbus import LINK_COLOR

        apply_indicator_states(self.chart, (state or {}).get("indicators") or [])
        gid = int((state or {}).get("link_group", 0) or 0)
        if gid not in LINK_COLOR:          # hand-edited / future-removed group -> unlinked
            gid = 0
        self.link_group = gid
        self._link_dot.set_group(gid)
        raw_ivl = (state or {}).get("interval_link_group", None)
        if raw_ivl is None:                 # legacy/older session -> follow symbol link
            self.interval_link_group = None
            self._ivl_dot.set_group(-1)
        else:
            igid = int(raw_ivl)
            if igid not in LINK_COLOR:       # hand-edited / removed group -> unlinked
                igid = 0
            self.interval_link_group = igid
            self._ivl_dot.set_group(igid)


class LiveHub(QtCore.QObject):
    """Round-robin live top-up for chart documents: one in-flight worker, visible docs only."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._docs: list[ChartDocument] = []
        self._worker = None              # round-robin LIVE-edge worker
        self._topup_worker = None        # one-shot INITIAL/STALE-load worker (a SECOND slot)
        self._pending_topup = None       # (doc, gen) deferred while _topup_worker is busy (latest-wins)
        self._cursor = 0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def register(self, doc: ChartDocument) -> None:
        if doc not in self._docs:
            self._docs.append(doc)
        doc._hub = self   # so doc.load() can request an off-thread top-up
        # Same test kill-switch as MainWindow._arm_live_updates: the offscreen suite must do no
        # real network I/O, and the hub's _LiveFetchWorker hits Binance/Yahoo. Never start the
        # round-robin timer under it (the merge logic is unit-tested in test_live_update.py).
        if not self._timer.isActive() and not os.environ.get("VIKE_DISABLE_LIVE"):
            self._timer.start(_HUB_TICK_MS)

    def unregister(self, doc: ChartDocument) -> None:
        if doc in self._docs:
            self._docs.remove(doc)
        doc._hub = None
        if self._pending_topup is not None and self._pending_topup[0] is doc:
            self._pending_topup = None
        if not self._docs:
            self._timer.stop()

    def request_topup(self, doc: ChartDocument, gen: int) -> None:
        """Off-thread top-up of ``doc``'s gap (initial/stale load), so a cold-symbol switch doesn't
        freeze the UI on inline REST pagination. Uses a SECOND worker slot that ``shutdown()`` waits
        on — it can't outlive the window and race the final GC (the 0xC0000409 teardown class). The
        worker is network-ONLY; the cache read + persist stay on the main thread (doc.load /
        apply_topup). Gated by VIKE_DISABLE_LIVE so the offscreen suite does no real network."""
        if os.environ.get("VIKE_DISABLE_LIVE"):
            return
        if self._topup_worker is not None:
            self._pending_topup = (doc, gen)   # latest-wins; fired when the slot frees
            return
        self._start_topup(doc, gen)

    def _start_topup(self, doc: ChartDocument, gen: int) -> None:
        from .app import _LiveFetchWorker  # late import: avoids an app<->chartdoc cycle

        now = int(time.time() * 1000)
        # Gap from the last cached bar (stale tail) or the full lookback window (cold) — MAIN thread.
        start = doc._bars[-1].ts if doc._bars else lookback_start(doc.interval, now)
        worker = self._topup_worker = _LiveFetchWorker(
            select_source(doc.symbol).fetch_bars_range, doc.symbol, doc.interval, start, now
        )
        worker.fetched.connect(lambda bars, d=doc, g=gen: self._on_topup_fetched(d, g, bars))
        worker.failed.connect(lambda msg, d=doc, g=gen: self._on_topup_failed(d, g, msg))
        worker.finished.connect(self._clear_topup_worker)
        worker.start()

    def _clear_topup_worker(self) -> None:
        self._topup_worker = None
        if self._pending_topup is not None:
            doc, gen = self._pending_topup
            self._pending_topup = None
            if doc in self._docs:
                self._start_topup(doc, gen)

    def _on_topup_fetched(self, doc: ChartDocument, gen: int, bars: list) -> None:
        if doc in self._docs:
            doc.apply_topup(gen, bars)

    def _on_topup_failed(self, doc: ChartDocument, gen: int, msg: str) -> None:
        if doc in self._docs:
            doc.topup_failed(gen, msg)

    def is_live(self) -> bool:
        """The round-robin poller is running — windows are being live-topped-up (honest LIVE
        vs CACHED gate, matching the main feed badge)."""
        return self._timer.isActive()

    def _tick(self) -> None:
        """Serve the next VISIBLE document. The fetch is network-only (off-thread safe);
        a still-running worker just skips the tick (no pile-up)."""
        if self._worker is not None or not self._docs:
            return
        visible = [d for d in self._docs if d._bars and d.isVisible()]
        if not visible:
            return
        from .app import _LiveFetchWorker  # late import: avoids an app<->chartdoc cycle

        doc = visible[self._cursor % len(visible)]
        self._cursor += 1
        now = int(time.time() * 1000)
        step = interval_ms(doc.interval)
        start, end = live_fetch_window(doc._bars[-1].ts, now, step, lookback=_LIVE_LOOKBACK)
        worker = self._worker = _LiveFetchWorker(
            select_source(doc.symbol).fetch_bars_range, doc.symbol, doc.interval, start, end
        )
        worker.fetched.connect(lambda bars, d=doc: self._on_fetched(d, bars))
        worker.failed.connect(lambda _msg: None)  # transient; next tick retries another doc
        worker.finished.connect(self._clear_worker)
        worker.start()

    def _clear_worker(self) -> None:
        self._worker = None

    def _on_fetched(self, doc: ChartDocument, bars: list) -> None:
        if doc in self._docs:
            doc.merge_live(bars)

    def shutdown(self) -> None:
        self._timer.stop()
        self._pending_topup = None
        # Wait BOTH worker slots: a top-up worker left running would race the interpreter's final
        # GC during teardown — the native 0xC0000409 class. This is the mandatory hardening.
        for attr in ("_worker", "_topup_worker"):
            w = getattr(self, attr)
            if w is not None:
                w.wait(2000)
                setattr(self, attr, None)
