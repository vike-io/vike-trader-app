"""Tests for LiveStrategyPump (Task 1, slice A2c).

Covers: start/stop lifecycle, warmup gate, order routing, event delivery via adapter,
robustness (on_bar exception does not kill the pump), NotImplementedError swallowing,
prime() warm-start, feed_bar guard after stop, strategy.index tracking, set_mark call.
"""

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec import events as ev
from vike_trader_app.exec.accounting import Account
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.exec.live_strategy_pump import LiveStrategyPump


class _Hub:
    """Minimal hub duck-type: the pump only needs account/venue/symbol/bus + submit_ticket/registry."""
    def __init__(self):
        self.account = Account(); self.venue = "binance"; self.symbol = "BTCUSDT"
        self.bus = EventBus(); self.tickets = []; self.registry = {}
    def submit_ticket(self, req): self.tickets.append(req)
    def cancel_ticket(self, coid): pass


class _Strat(Strategy):
    WARMUP = 2
    def __init__(self): self.started = False; self.stopped = False; self.bars = []; self.events = []
    def on_start(self): self.started = True
    def on_stop(self): self.stopped = True
    def on_bar(self, bar): self.bars.append(bar); self.buy(1.0)   # buy -> engine.submit -> hub.submit_ticket
    def on_event(self, e): self.events.append(e)


def _bar(ts, px=100.0): return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)


def test_start_calls_on_start_and_injects_engine():
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub)
    p.start()
    assert s.started is True
    assert s._engine is p.engine            # engine injected
    p.stop()


def test_feed_bar_warmup_gate_then_on_bar_routes_orders():
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    p.feed_bar(_bar(1)); p.feed_bar(_bar(2))   # i=0,1 < WARMUP=2 -> no on_bar
    assert s.bars == [] and hub.tickets == []
    p.feed_bar(_bar(3))                         # i=2 >= WARMUP -> on_bar fires, buy routes
    assert len(s.bars) == 1 and len(hub.tickets) == 1
    p.stop()


def test_events_reach_strategy_via_adapter():
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    hub.bus.publish(ev.OrderAccepted(client_order_id="c1"))
    assert any(isinstance(e, ev.OrderAccepted) for e in s.events)
    p.stop()


def test_stop_calls_on_stop_and_unsubscribes():
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    p.stop()
    assert s.stopped is True
    s.events.clear()
    hub.bus.publish(ev.OrderAccepted(client_order_id="c2"))   # adapter unsubscribed -> no delivery
    assert s.events == []


def test_on_bar_exception_does_not_kill_pump():
    class _Boom(_Strat):
        def on_bar(self, bar): raise RuntimeError("boom")
    s = _Boom(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    p.feed_bar(_bar(1)); p.feed_bar(_bar(2)); p.feed_bar(_bar(3))   # must not raise
    p.feed_bar(_bar(4))                                             # still alive
    p.stop()


def test_not_implemented_order_type_is_swallowed():
    class _Stop(_Strat):
        def on_bar(self, bar): self.submit_stop(1.0, 99.0)   # StrategyLiveEngine raises NotImplementedError
    s = _Stop(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    p.feed_bar(_bar(1)); p.feed_bar(_bar(2)); p.feed_bar(_bar(3))   # must not raise
    p.stop()


# ---------------------------------------------------------------------------
# Fix A: prime() warms the buffer + advances index WITHOUT firing on_bar
# ---------------------------------------------------------------------------

def test_prime_warms_buffer_without_on_bar():
    """prime() loads bars into engine.bars and advances _i but NEVER calls on_bar."""
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub)
    history = [_bar(i, px=float(i + 1)) for i in range(5)]
    p.prime(history)
    # on_bar must NOT have fired during prime
    assert s.bars == []
    assert hub.tickets == []
    # The engine buffer must be populated
    assert len(p.engine.bars) == 5
    # _i must be advanced (5 bars primed -> _i == 4)
    assert p._i == 4
    p.start()
    p.stop()


def test_prime_advances_strategy_index():
    """strategy.index must equal _i after prime."""
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub)
    history = [_bar(i) for i in range(3)]
    p.prime(history)
    assert s.index == p._i == 2
    p.start()
    p.stop()


def test_prime_then_feed_bar_respects_warmup():
    """After priming with WARMUP+ bars, the very first live feed_bar should fire on_bar."""
    class _NoWarmup(Strategy):
        WARMUP = 0
        def __init__(self): self.bars = []
        def on_bar(self, bar): self.bars.append(bar)

    s = _NoWarmup(); hub = _Hub(); p = LiveStrategyPump(s, hub)
    # Prime with 3 bars (already past any warmup=0)
    p.prime([_bar(i) for i in range(3)])
    p.start()
    # First live bar should fire on_bar immediately (_i=3 >= WARMUP=0)
    p.feed_bar(_bar(10))
    assert len(s.bars) == 1
    p.stop()


def test_prime_sets_mark_on_account():
    """prime() must call set_mark from each bar's close so order_target_percent can work."""
    hub = _Hub(); s = _Strat(); p = LiveStrategyPump(s, hub)
    bars = [_bar(i, px=float(200 + i)) for i in range(3)]
    p.prime(bars)
    # The last bar's close should be the mark.
    mark = hub.account.marks.get(("binance", "BTCUSDT"))
    assert mark == 202.0  # last bar close = 200 + 2
    p.start()
    p.stop()


# ---------------------------------------------------------------------------
# Fix B: feed_bar is a no-op after stop()
# ---------------------------------------------------------------------------

def test_feed_bar_noop_after_stop():
    """feed_bar must return immediately if _started is False (after stop())."""
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    # Warm up past WARMUP so next bar would fire on_bar.
    p.feed_bar(_bar(1)); p.feed_bar(_bar(2)); p.feed_bar(_bar(3))
    assert len(s.bars) == 1   # first live bar fired on_bar
    p.stop()
    # After stop, feed_bar must not call on_bar or route orders.
    prev_bars = len(s.bars)
    prev_tickets = len(hub.tickets)
    p.feed_bar(_bar(4))
    assert len(s.bars) == prev_bars
    assert len(hub.tickets) == prev_tickets


def test_feed_bar_noop_before_start():
    """feed_bar must be a no-op if start() was never called (_started=False initially)."""
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub)
    p.feed_bar(_bar(1))
    assert s.bars == []
    assert hub.tickets == []
    p.start()
    p.stop()


# ---------------------------------------------------------------------------
# Fix C: strategy.index advances including across the warmup gate
# ---------------------------------------------------------------------------

def test_strategy_index_advances_through_warmup():
    """strategy.index must be updated on EVERY feed_bar call, including warmup-gated bars."""
    s = _Strat(); hub = _Hub(); p = LiveStrategyPump(s, hub); p.start()
    # WARMUP=2, so first 2 bars (i=0,1) are warmup-gated
    p.feed_bar(_bar(1))
    assert s.index == 0
    p.feed_bar(_bar(2))
    assert s.index == 1
    p.feed_bar(_bar(3))   # first live bar (i=2 >= WARMUP=2)
    assert s.index == 2
    p.stop()


# ---------------------------------------------------------------------------
# Fix D: set_mark called from bar.close so order_target_percent routes on spot
# ---------------------------------------------------------------------------

def test_feed_bar_sets_mark_on_account():
    """feed_bar must call hub.account.set_mark with bar.close after add_live_bar."""
    hub = _Hub(); s = _Strat(); p = LiveStrategyPump(s, hub); p.start()
    p.feed_bar(_bar(1, px=555.0))
    mark = hub.account.marks.get(("binance", "BTCUSDT"))
    assert mark == 555.0
    p.feed_bar(_bar(2, px=777.0))
    mark = hub.account.marks.get(("binance", "BTCUSDT"))
    assert mark == 777.0
    p.stop()


def test_order_target_percent_routes_after_mark_set():
    """With set_mark called by feed_bar, order_target_percent must route an order."""
    class _PctStrat(Strategy):
        WARMUP = 0
        def on_bar(self, bar):
            self.order_target_percent(1.0)   # target 100% equity long

    hub = _Hub()
    hub.account.balance = 10_000.0   # give it equity to size from
    s = _PctStrat(); p = LiveStrategyPump(s, hub); p.start()
    p.feed_bar(_bar(1, px=100.0))   # mark=100 after set_mark; equity=10000 -> target 100 units
    assert len(hub.tickets) == 1   # order routed
    p.stop()
