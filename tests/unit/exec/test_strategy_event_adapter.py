from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec import events as ev
from vike_trader_app.exec.strategy_event_adapter import StrategyEventAdapter


class _Strat:
    def __init__(self): self.calls = []
    def on_order_submitted(self, e): self.calls.append(("submitted", e))
    def on_order_accepted(self, e): self.calls.append(("accepted", e))
    def on_order_rejected(self, e): self.calls.append(("rejected", e))
    def on_order_canceled(self, e): self.calls.append(("canceled", e))
    def on_order_expired(self, e): self.calls.append(("expired", e))
    def on_order_filled(self, fill): self.calls.append(("filled", fill))
    def on_position_opened(self, p): self.calls.append(("opened", p))
    def on_position_changed(self, p): self.calls.append(("changed", p))
    def on_position_closed(self, p): self.calls.append(("closed", p))
    def on_liquidation(self, fill): self.calls.append(("liq", fill))
    def on_event(self, e): self.calls.append(("event", type(e).__name__))


def _wire():
    bus = EventBus(); s = _Strat(); StrategyEventAdapter(s, bus); return bus, s


def _fe(qty=2.0, px=100.0):
    return ev.FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol="BTCUSDT",
                        side=+1, last_qty=qty, last_px=px, commission=0.1, liquidity_side="maker", ts=5)


def test_order_lifecycle_dispatch():
    bus, s = _wire()
    bus.publish(ev.OrderSubmitted(client_order_id="c1"))
    bus.publish(ev.OrderAccepted(client_order_id="c1"))
    bus.publish(ev.OrderRejected(client_order_id="c1", reason="x"))
    bus.publish(ev.OrderDenied(client_order_id="c2", reason="risk"))
    bus.publish(ev.OrderCanceled(client_order_id="c1"))
    bus.publish(ev.OrderExpired(client_order_id="c1"))
    kinds = [k for k, _ in s.calls if k != "event"]
    assert kinds == ["submitted", "accepted", "rejected", "rejected", "canceled", "expired"]  # Denied->rejected
    assert sum(1 for k, _ in s.calls if k == "event") == 6  # on_event for every event


def test_fill_maps_to_core_fill():
    bus, s = _wire()
    bus.publish(ev.OrderFilled(client_order_id="c1", fill=_fe(qty=2.0, px=100.0)))
    filled = [v for k, v in s.calls if k == "filled"]
    assert len(filled) == 1
    f = filled[0]
    assert (f.side, f.size, f.price, f.fee, f.ts, f.is_maker, f.symbol) == (1, 2.0, 100.0, 0.1, 5, True, "BTCUSDT")


def test_partial_fill_also_fires_on_order_filled():
    bus, s = _wire()
    bus.publish(ev.OrderPartiallyFilled(client_order_id="c1", fill=_fe()))
    assert any(k == "filled" for k, _ in s.calls)


def test_handler_exception_does_not_break_bus():
    bus = EventBus()
    class _Boom(_Strat):
        def on_order_filled(self, fill): raise RuntimeError("boom")
    other = []
    bus.subscribe(lambda e: other.append(e))   # another subscriber must still receive
    StrategyEventAdapter(_Boom(), bus)
    bus.publish(ev.OrderFilled(client_order_id="c1", fill=_fe()))   # must not raise
    assert other                                # the other subscriber still got the event
