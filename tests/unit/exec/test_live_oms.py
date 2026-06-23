"""LiveOmsHub: gate->OrderDenied/submit, dual-publish folds Account once per trade_id, snapshot seed."""

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderDenied,
    OrderFilled,
    OrderRequest,
    OrderSubmitted,
)
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.order import ManagedOrder, OrderStatus
from vike_trader_app.exec.risk import RiskGate, RiskLimits, TradingState


class _SpyClient:
    def __init__(self):
        self.submitted = []

    def submit(self, request):
        self.submitted.append(request)

    def detach(self):
        self.detached = True


def _hub(gate=None, client=None):
    bus = EventBus()
    return LiveOmsHub(bus=bus, account=Account(), gate=gate or RiskGate(RiskLimits()),
                      client=client or _SpyClient(), venue="binance", symbol="BTCUSDT")


def _req(coid="s-0", side=+1, qty=1.0):
    return OrderRequest(client_order_id=coid, venue="binance", symbol="BTCUSDT",
                        side=side, qty=qty, order_type="limit", price=100.0)


def test_submit_ticket_routes_ok_order_to_client():
    client = _SpyClient()
    hub = _hub(client=client)
    hub.submit_ticket(_req())
    assert len(client.submitted) == 1
    assert client.submitted[0].client_order_id == "s-0"


def test_submit_ticket_publishes_order_denied_on_veto():
    gate = RiskGate(RiskLimits())
    hub = _hub(gate=gate)
    denied = []
    hub.bus.subscribe(lambda e: denied.append(e) if isinstance(e, OrderDenied) else None)
    hub._trading_state = TradingState.HALTED   # kill switch
    hub.submit_ticket(_req())
    assert len(denied) == 1
    assert denied[0].client_order_id == "s-0"
    assert denied[0].reason == "halted"


def test_dual_publish_folds_account_exactly_once_per_trade_id():
    hub = _hub()
    # register the order so the wrapping OrderFilled is a legal FSM edge
    hub.registry["s-0"] = ManagedOrder(request=_req(), status=OrderStatus.ACCEPTED)
    fill = FillEvent(trade_id="t1", client_order_id="s-0", venue="binance", symbol="BTCUSDT",
                     side=+1, last_qty=1.0, last_px=100.0)
    hub.bus.publish(fill)                                   # bare -> Account
    hub.bus.publish(OrderFilled(client_order_id="s-0", fill=fill))   # wrap -> FSM only
    pos = hub.account.positions[("binance", "BTCUSDT", "BOTH")]
    assert pos["size"] == 1.0   # folded ONCE, not twice
    assert hub.registry["s-0"].status is OrderStatus.FILLED


def test_apply_snapshot_seeds_position_and_registry():
    from vike_trader_app.exec.binance.client import ReconcileSnapshot

    hub = _hub()
    mo = ManagedOrder(request=_req(coid="prev-1"), status=OrderStatus.ACCEPTED, venue_order_id="9")
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", 0.5),), open_orders=(mo,)))
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.5
    assert hub.registry["prev-1"] is mo


def test_shutdown_detaches_and_unsubscribes():
    client = _SpyClient()
    hub = _hub(client=client)
    hub.shutdown()
    assert getattr(client, "detached", False)
    # bus no longer delivers to the hub
    hub.bus.publish(OrderSubmitted(client_order_id="x"))
    assert "x" not in hub.registry


def test_lifecycle_event_drives_registry():
    hub = _hub()
    hub.registry["s-0"] = ManagedOrder(request=_req())
    hub.bus.publish(OrderSubmitted(client_order_id="s-0"))
    hub.bus.publish(OrderAccepted(client_order_id="s-0", venue_order_id="11"))
    assert hub.registry["s-0"].status is OrderStatus.ACCEPTED
    assert hub.registry["s-0"].venue_order_id == "11"
