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
        outer.addWidget(card)

        self.chart.intervalChosen.connect(lambda iv: self.load(interval=iv))

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
        self._symbol = (symbol or self._symbol).upper()
        self._interval = interval or self._interval
        res = load_symbol_bars(self._symbol, self._interval, int(time.time() * 1000),
                               network=network)
        if not res.ok:
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
        background documents load cache-only at startup (no network storm)."""
        if not self._loaded:
            self.load()

    def merge_live(self, fetched: list) -> None:
        """Main thread: merge live-edge bars from the hub and repaint if anything changed."""
        if not self._bars or not fetched:
            return
        merged, appended, replaced_last = merge_live_bars(self._bars, fetched)
        if appended or replaced_last:
            self._bars = merged
            self.chart.apply_live(merged, None, repaint=False)
            self.chart.show_upto(len(merged) - 1)

    # --- persistence ------------------------------------------------------------------------

    def state(self) -> dict:
        return {"symbol": self._symbol, "interval": self._interval,
                "indicators": indicator_states(self.chart)}

    def apply_state(self, state: dict) -> None:
        """Re-attach saved indicators (call after a successful load put bars on the chart)."""
        apply_indicator_states(self.chart, (state or {}).get("indicators") or [])


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
