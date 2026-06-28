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

    def feed_bar(self, bar) -> None:
        """One CLOSED bar (main thread): update the MTF buffer, then warmup-gated on_bar."""
        self.engine.add_live_bar(bar)
        self._i += 1
        if self._i < getattr(self.strategy, "WARMUP", 0):
            return
        try:
            self.strategy.on_bar(bar)
        except NotImplementedError:
            log.warning("strategy used a not-yet-supported order type (stop/trailing -> A2e); skipped")
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
