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
from .dataload import load_symbol_bars
from .session import apply_indicator_states, indicator_states

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


class ChartDocument(QtWidgets.QWidget):
    """A clean, standalone chart viewer document (PriceChart + oscillator pane host)."""

    symbolChanged = QtCore.Signal(str, str)   # (symbol, interval) — feeds tab title + link groups

    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h", parent=None):
        super().__init__(parent)
        from .chart import PriceChart  # heavy import kept local (pyqtgraph)

        self._symbol = symbol
        self._interval = interval
        self._bars: list = []
        self._loaded = False         # becomes True after the first real load attempt

        self.chart = PriceChart()
        # same rounded-card treatment as the Chart space (transparent viewport on a card)
        self.chart.setBackground(None)
        vp = self.chart.viewport()
        vp.setAutoFillBackground(False)
        vp.setStyleSheet("background:transparent;")
        self.chart.setViewportUpdateMode(QtWidgets.QGraphicsView.FullViewportUpdate)
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
        split.addWidget(self.chart)
        split.setStretchFactor(0, 1)
        self.chart.set_pane_host(split)
        card_lay.addWidget(split)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
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
        # The dots live IN the chart's top toolbar (MultiCharts puts link colours in the chart's
        # status line) — NOT a separate header row (user-rejected: "why another row").
        self._link_dot = LinkDot(0, label="Symbol")
        self._link_dot.groupChanged.connect(self._set_link_group)
        self.chart.add_toolbar_widget(self._link_dot)
        self._ivl_dot = LinkDot(-1, label="Interval", glyph=("◇", "◆"), follow=True)
        self._ivl_dot.groupChanged.connect(self._set_interval_link_group)
        self.chart.add_toolbar_widget(self._ivl_dot)
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
        """(Re)load this document's series (cache-first, MAIN THREAD). Returns success."""
        prev_symbol, prev_interval = self._symbol, self._interval
        self._symbol = (symbol or self._symbol).upper()
        self._interval = interval or self._interval
        res = load_symbol_bars(self._symbol, self._interval, int(time.time() * 1000),
                               network=network)
        if not res.ok:
            # Roll back identity on a failed load so the doc keeps showing its real series —
            # otherwise a failed apply_link (bad symbol via a link broadcast) would corrupt
            # _symbol, mislabel the tab, and re-broadcast the bad symbol to linked peers.
            self._symbol, self._interval = prev_symbol, prev_interval
            return False
        # Mark "loaded" only after a SUCCESSFUL network round-trip — otherwise a failed
        # network "+New chart" (bad symbol / offline) would latch _loaded=True and ensure_loaded
        # would never retry, leaving the doc stuck empty. A cache-only (network=False) load
        # never latches, so restored docs always top up on first focus.
        self._loaded = self._loaded or network
        self._bars = res.bars
        self.chart.set_data(self._bars, [])
        self.chart.set_overlays({})
        self.chart.set_title(self._symbol)
        self.chart.set_timeframe(self._interval)
        self.chart.show_upto(len(self._bars) - 1)
        self.symbolChanged.emit(self._symbol, self._interval)
        return True

    def ensure_loaded(self) -> None:
        """Top up over the network the first time the document is actually shown — restored
        background documents load cache-only at startup (no network storm).

        This is a focus-triggered top-up of THIS doc, not a user symbol change, so it must NOT
        broadcast to link peers: a restored doc carries the saved link group by now, and without
        the guard, simply focusing it would overwrite same-group peers with its stale symbol."""
        if not self._loaded:
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
        self._worker = None
        self._cursor = 0
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def register(self, doc: ChartDocument) -> None:
        if doc not in self._docs:
            self._docs.append(doc)
        # Same test kill-switch as MainWindow._arm_live_updates: the offscreen suite must do no
        # real network I/O, and the hub's _LiveFetchWorker hits Binance/Yahoo. Never start the
        # round-robin timer under it (the merge logic is unit-tested in test_live_update.py).
        if not self._timer.isActive() and not os.environ.get("VIKE_DISABLE_LIVE"):
            self._timer.start(_HUB_TICK_MS)

    def unregister(self, doc: ChartDocument) -> None:
        if doc in self._docs:
            self._docs.remove(doc)
        if not self._docs:
            self._timer.stop()

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
        if self._worker is not None:
            self._worker.wait(2000)
            self._worker = None
