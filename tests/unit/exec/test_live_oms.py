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
    # No position_avg_px supplied -> falls back to 0.0 (backwards-compat)
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["avg_px"] == 0.0
    assert hub.registry["prev-1"] is mo


def test_apply_snapshot_seeds_avg_px_from_snapshot():
    """apply_snapshot must seed avg_px from position_avg_px, not hardcode 0.0."""
    from vike_trader_app.exec.binance.client import ReconcileSnapshot

    hub = _hub()
    snap = ReconcileSnapshot(
        positions=(("BTCUSDT", 0.5),),
        open_orders=(),
        position_avg_px=(("BTCUSDT", 68000.0),),
    )
    hub.apply_snapshot(snap)
    pos = hub.account.positions[("binance", "BTCUSDT", "BOTH")]
    assert pos["size"] == 0.5
    assert pos["avg_px"] == 68000.0  # from snapshot, NOT hardcoded 0.0


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


def test_reconnect_replay_does_not_double_fold_without_exec_db():
    """Fix 1: in-memory dedup must block a replayed FillEvent even when exec_db is None."""
    hub = _hub()   # no exec_db_conn
    hub.registry["s-0"] = ManagedOrder(request=_req(), status=OrderStatus.ACCEPTED)
    fill = FillEvent(trade_id="t-replay", client_order_id="s-0", venue="binance",
                     symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=100.0)
    hub.bus.publish(fill)   # first delivery — should fold
    hub.bus.publish(fill)   # WS reconnect replay — must be dropped
    pos = hub.account.positions[("binance", "BTCUSDT", "BOTH")]
    assert pos["size"] == 1.0   # folded ONCE


def test_foreign_symbol_fill_is_ignored():
    """Fix 2: the Bybit execution stream is account-wide; a fill for a DIFFERENT symbol on the
    shared demo account must NOT fold into this single-symbol hub's Account."""
    hub = _hub()   # symbol="BTCUSDT"
    foreign = FillEvent(trade_id="f-1", client_order_id="other", venue="binance",
                        symbol="ETHUSDT", side=+1, last_qty=2.0, last_px=3000.0)
    hub.bus.publish(foreign)
    # No spurious ETHUSDT position created
    assert ("binance", "ETHUSDT", "BOTH") not in hub.account.positions
    # And no BTCUSDT position either (nothing folded at all)
    assert ("binance", "BTCUSDT", "BOTH") not in hub.account.positions


def test_matching_symbol_fill_still_folds():
    """Fix 2 regression guard: a fill for the hub's own symbol still folds into the Account."""
    hub = _hub()   # symbol="BTCUSDT"
    hub.registry["s-0"] = ManagedOrder(request=_req(), status=OrderStatus.ACCEPTED)
    fill = FillEvent(trade_id="m-1", client_order_id="s-0", venue="binance",
                     symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=100.0)
    hub.bus.publish(fill)
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 1.0


def test_ws_replay_of_accept_on_seeded_order_does_not_crash():
    """Fix 2: replaying OrderAccepted for a snapshot-seeded ACCEPTED order must not raise."""
    from vike_trader_app.exec.binance.client import ReconcileSnapshot

    hub = _hub()
    mo = ManagedOrder(request=_req(coid="prev-1"), status=OrderStatus.ACCEPTED, venue_order_id="9")
    hub.apply_snapshot(ReconcileSnapshot(positions=(("BTCUSDT", 0.5),), open_orders=(mo,)))
    # WS replays x=NEW -> OrderAccepted for an order already at ACCEPTED: must not raise
    hub.bus.publish(OrderAccepted(client_order_id="prev-1", venue_order_id="9"))
    assert hub.registry["prev-1"].status is OrderStatus.ACCEPTED


# ---------------------------------------------------------------------------
# Task 2 — Registry-on-submit
# ---------------------------------------------------------------------------

def test_submit_ticket_registers_managed_order():
    """submit_ticket must insert a ManagedOrder into registry BEFORE client.submit."""
    client = _SpyClient()  # only records; publishes nothing
    hub = _hub(client=client)
    hub.submit_ticket(_req("c-1"))
    assert "c-1" in hub.registry
    assert hub.registry["c-1"].request.client_order_id == "c-1"
    assert hub.registry["c-1"].status is OrderStatus.INITIALIZED


class _SyncLifecycleClient:
    """Mimics the real REST client: submit() synchronously publishes OrderSubmitted then OrderAccepted."""

    def __init__(self, bus, venue_order_id="42"):
        self._bus = bus
        self._venue_order_id = venue_order_id
        self.submitted = []

    def submit(self, request):
        self.submitted.append(request)
        self._bus.publish(OrderSubmitted(client_order_id=request.client_order_id))
        self._bus.publish(OrderAccepted(client_order_id=request.client_order_id,
                                        venue_order_id=self._venue_order_id))


def test_submit_then_synchronous_lifecycle_advances_fsm():
    """After submit_ticket, the re-entrant OrderSubmitted/OrderAccepted must advance the FSM.

    This proves the order is registered BEFORE client.submit() publishes (else the events
    find an absent registry entry and the order stays stuck at INITIALIZED).
    """
    bus = EventBus()
    client = _SyncLifecycleClient(bus, venue_order_id="42")
    hub = LiveOmsHub(bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
                     client=client, venue="binance", symbol="BTCUSDT")
    hub.submit_ticket(_req("c-2"))
    assert hub.registry["c-2"].status is OrderStatus.ACCEPTED
    assert hub.registry["c-2"].venue_order_id == "42"


def test_submit_ticket_veto_does_not_register():
    """A gate-vetoed order (OrderDenied) must leave no registry entry."""
    hub = _hub()
    hub._trading_state = TradingState.HALTED
    hub.submit_ticket(_req("c-3"))
    assert "c-3" not in hub.registry


def test_submit_registers_rounded_request():
    """The registered ManagedOrder must use verdict.request (lot-rounded), not the raw request."""
    client = _SpyClient()
    gate = RiskGate(RiskLimits(lot_size=0.01))
    hub = _hub(gate=gate, client=client)
    raw = _req("c-4", qty=1.2345)
    hub.submit_ticket(raw)
    # gate rounds 1.2345 to nearest 0.01 => 1.23
    registered_qty = hub.registry["c-4"].request.qty
    submitted_qty = client.submitted[0].qty
    assert registered_qty == submitted_qty  # both use the rounded verdict.request
