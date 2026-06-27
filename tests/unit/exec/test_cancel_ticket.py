from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.order import ManagedOrder, OrderStatus


class _Bus:
    def __init__(self): self.subs = []
    def subscribe(self, cb): self.subs.append(cb)
    def unsubscribe(self, cb): self.subs.remove(cb)
    def publish(self, ev): pass


class _Client:
    def __init__(self): self.cancelled = []
    def submit(self, req): pass
    def cancel(self, coid): self.cancelled.append(coid)


class _Gate:
    def check(self, req, ctx): raise AssertionError("cancel must not touch the gate")


def _hub():
    return LiveOmsHub(bus=_Bus(), account=type("A", (), {"positions": {}, "marks": {}})(),
                      gate=_Gate(), client=_Client(), venue="binance", symbol="BTCUSDT")


def _put(hub, coid, status):
    req = OrderRequest(client_order_id=coid, venue="binance", symbol="BTCUSDT",
                       side=1, qty=0.01, order_type="limit", price=65000.0)
    hub.registry[coid] = ManagedOrder(request=req, status=status)


def test_cancel_live_order_calls_client_cancel():
    hub = _hub()
    _put(hub, "c1", OrderStatus.ACCEPTED)
    hub.cancel_ticket("c1")
    assert hub.client.cancelled == ["c1"]


def test_cancel_partially_filled_is_allowed():
    hub = _hub()
    _put(hub, "c1", OrderStatus.PARTIALLY_FILLED)
    hub.cancel_ticket("c1")
    assert hub.client.cancelled == ["c1"]


def test_cancel_unknown_coid_is_noop():
    hub = _hub()
    hub.cancel_ticket("nope")
    assert hub.client.cancelled == []


def test_cancel_terminal_order_is_noop():
    hub = _hub()
    for st in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED,
               OrderStatus.DENIED, OrderStatus.EXPIRED, OrderStatus.LIQUIDATED):
        hub.registry.clear()
        _put(hub, "c1", st)
        hub.cancel_ticket("c1")
    assert hub.client.cancelled == []


def test_cancel_publishes_nothing():
    hub = _hub()
    published = []
    hub.bus.publish = published.append
    _put(hub, "c1", OrderStatus.ACCEPTED)
    hub.cancel_ticket("c1")
    assert published == []     # FSM advance is WS-driven, not synthesized here
