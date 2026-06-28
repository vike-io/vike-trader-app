"""Drive a coded Strategy live (the live analogue of running it through BacktestEngine).

Qt-free + single-threaded: start()/feed_bar()/stop() are called only on the Qt main thread (the same
thread as EventBus.publish). A worker (ui.live_feed_worker) marshals closed bars here via a queued
signal. Orders route through StrategyLiveEngine -> LiveOmsHub (RiskGate inside); venue events reach the
strategy's A1 handlers via StrategyEventAdapter (subscribed AFTER the hub, so handlers see settled state).
"""

import logging

from .strategy_event_adapter import StrategyEventAdapter
from .strategy_live_engine import StrategyLiveEngine

log = logging.getLogger(__name__)


class LiveStrategyPump:
    def __init__(self, strategy, hub, *, multiplier: float = 1.0, timeframes=None, now_ms=None) -> None:
        self.strategy = strategy
        self._hub = hub
        self.engine = StrategyLiveEngine(hub, hub.account, hub.venue, hub.symbol,
                                         multiplier=multiplier, timeframes=timeframes, now_ms=now_ms)
        strategy._engine = self.engine
        self._adapter = StrategyEventAdapter(strategy, hub.bus)   # subscribes AFTER the hub
        self._i = -1
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        try:
            self.strategy.on_start()
        except Exception:  # noqa: BLE001 - a strategy bug must not abort arming
            log.exception("strategy.on_start raised")
        self._started = True

    def prime(self, bars) -> None:
        """Warm the engine MTF buffer + advance the bar index WITHOUT firing on_bar.

        Call this BEFORE start() + before the live worker begins feeding bars.  Each bar
        is pushed through ``engine.add_live_bar`` (which populates the MTF resampler and
        appends to ``engine.bars`` exactly as live feed_bar calls do), the index counter
        ``_i`` is advanced, and ``strategy.index`` is kept in sync — so that when the
        first live ``feed_bar`` arrives the warmup gate is already satisfied (assuming the
        caller supplied at least ``WARMUP`` history bars) and the strategy's indicator
        lookbacks can reference a pre-populated ``engine.bars`` buffer.

        The mark price is updated from each bar's close via ``hub.account.set_mark`` so
        that ``order_target_percent`` / ``order_target_value`` has a valid reference price
        from the very first live bar.

        NOTE: ``on_start`` is NOT called here; call ``start()`` separately AFTER priming.
        ``on_bar`` is intentionally NOT called for primed bars — they are purely history.
        """
        for bar in bars:
            self.engine.add_live_bar(bar)
            self._i += 1
            self.strategy.index = self._i
            # Keep the account mark current so order_target_percent works from bar 1.
            self._hub.account.set_mark(self._hub.venue, self._hub.symbol, bar.close)

    def feed_bar(self, bar) -> None:
        """One CLOSED bar (main thread): update the MTF buffer, then warmup-gated on_bar."""
        if not self._started:
            return  # B: guard late queued bars arriving after stop()
        self.engine.add_live_bar(bar)
        self._i += 1
        self.strategy.index = self._i  # C: keep strategy.index in sync including across warmup gate
        # D: update the mark so order_target_percent / order_target_value has a valid reference price.
        self._hub.account.set_mark(self._hub.venue, self._hub.symbol, bar.close)
        # E: fire triggered conditionals BEFORE on_bar (fills precede decisions, matching backtest).
        # Conditionals are armed explicitly by the strategy and fire regardless of the warmup gate.
        try:
            self.engine.check_conditionals(bar)
        except Exception:  # noqa: BLE001
            log.exception("check_conditionals raised; pump continues")
        if self._i < getattr(self.strategy, "WARMUP", 0):
            return
        try:
            self.strategy.on_bar(bar)
        except Exception:  # noqa: BLE001 - live robustness: a strategy bug must not crash the session
            log.exception("strategy.on_bar raised; pump continues (disarm to stop)")

    def stop(self) -> None:
        if self._started:
            try:
                self.strategy.on_stop()
            except Exception:  # noqa: BLE001
                log.exception("strategy.on_stop raised")
        self._adapter.unsubscribe()
        self._started = False
