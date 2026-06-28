"""Tests for LiveStrategyPump (Task 1, slice A2c).

Covers: start/stop lifecycle, warmup gate, order routing, event delivery via adapter,
robustness (on_bar exception does not kill the pump), NotImplementedError swallowing.
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
