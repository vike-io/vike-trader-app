"""Pure executionReport mapper: each x/X combo -> the right vike event(s); dual-publish on TRADE."""

import pytest

from vike_trader_app.exec.binance.mapper import map_binance_private, map_execution_report
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


def _frame(**kw):
    base = {"e": "executionReport", "s": "BTCUSDT", "c": "sess-1", "S": "BUY",
            "x": "NEW", "X": "NEW", "i": 42, "l": "0", "L": "0", "n": "0",
            "m": False, "t": -1, "T": 1700, "r": "NONE"}
    base.update(kw)
    return base


def test_new_maps_to_order_accepted():
    out = map_execution_report(_frame(x="NEW", X="NEW"), venue="binance", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderAccepted)
    assert out[0].client_order_id == "sess-1"
    assert out[0].venue_order_id == "42"


def test_trade_partial_emits_fill_and_partially_filled():
    f = _frame(x="TRADE", X="PARTIALLY_FILLED", l="0.5", L="65000", n="0.01", m=True, t=777)
    out = map_execution_report(f, venue="binance", symbol="BTCUSDT")
    fills = [e for e in out if isinstance(e, FillEvent)]
    wraps = [e for e in out if isinstance(e, OrderPartiallyFilled)]
    assert len(fills) == 1 and len(wraps) == 1
    fe = fills[0]
    assert (fe.trade_id, fe.client_order_id, fe.side) == ("777", "sess-1", +1)
    assert (fe.last_qty, fe.last_px, fe.commission, fe.liquidity_side) == (0.5, 65000.0, 0.01, "maker")
    assert wraps[0].fill is fe  # the wrap carries the SAME FillEvent


def test_trade_filled_emits_fill_and_filled():
    f = _frame(x="TRADE", X="FILLED", l="1.0", L="65000", S="SELL", m=False, t=888)
    out = map_execution_report(f, venue="binance", symbol="BTCUSDT")
    assert any(isinstance(e, FillEvent) and e.side == -1 and e.liquidity_side == "taker" for e in out)
    assert any(isinstance(e, OrderFilled) for e in out)


def test_canceled_rejected_expired():
    assert isinstance(map_execution_report(_frame(x="CANCELED", X="CANCELED"),
                      venue="binance", symbol="BTCUSDT")[0], OrderCanceled)
    rej = map_execution_report(_frame(x="REJECTED", X="REJECTED", r="INSUFFICIENT_BALANCE"),
                               venue="binance", symbol="BTCUSDT")[0]
    assert isinstance(rej, OrderRejected) and rej.reason == "INSUFFICIENT_BALANCE"
    assert isinstance(map_execution_report(_frame(x="EXPIRED", X="EXPIRED"),
                      venue="binance", symbol="BTCUSDT")[0], OrderExpired)


def test_unknown_exec_type_is_ignored():
    assert map_execution_report(_frame(x="TRADE_PREVENTION"), venue="binance", symbol="BTCUSDT") == []


def test_frame_symbol_overrides_passed_symbol():
    """Account-wide WS: frame `s` takes precedence over the caller's `symbol` argument."""
    frame = _frame(x="TRADE", X="FILLED", s="ETHUSDT", l="1.0", L="3000", m=False, t=999)
    out = map_execution_report(frame, venue="binance", symbol="BTCUSDT")
    fills = [e for e in out if isinstance(e, FillEvent)]
    assert len(fills) == 1
    assert fills[0].symbol == "ETHUSDT"  # frame's `s`, NOT the passed "BTCUSDT"


# ---------------------------------------------------------------------------
# Task 2 — map_binance_private WS-API envelope dispatcher
# ---------------------------------------------------------------------------

def _wrapped(inner):
    return {"subscriptionId": 0, "event": inner}


def test_dispatcher_unwraps_wsapi_envelope_and_dual_publishes():
    inner = _frame(x="TRADE", X="FILLED", l="1.0", L="65000", n="0.01", m=False, t=555)
    out = map_binance_private(_wrapped(inner), venue="binance", symbol="BTCUSDT")
    fills = [e for e in out if isinstance(e, FillEvent)]
    wraps = [e for e in out if isinstance(e, OrderFilled)]
    assert len(fills) == 1 and len(wraps) == 1
    assert fills[0].trade_id == "555"
    assert wraps[0].fill is fills[0]               # wrap carries the SAME FillEvent


def test_dispatcher_tolerates_raw_unwrapped_executionReport():
    inner = _frame(x="TRADE", X="PARTIALLY_FILLED", l="0.5", L="100", t=7)
    out = map_binance_private(inner, venue="binance", symbol="BTCUSDT")  # no envelope
    assert any(isinstance(e, FillEvent) for e in out)
    assert any(isinstance(e, OrderPartiallyFilled) for e in out)


def test_dispatcher_ignores_subscribe_ack_frame():
    assert map_binance_private({"id": "r1", "status": 200, "result": {"subscriptionId": 0}}) == []


def test_dispatcher_ignores_error_ack_and_non_dict():
    assert map_binance_private({"id": "r1", "status": 400, "error": {"code": -2010, "msg": "x"}}) == []
    assert map_binance_private("pong") == []
    assert map_binance_private({"event": {"e": "outboundAccountPosition"}}) == []


def test_dispatcher_drives_managed_order_fsm_to_filled():
    """Offline LiveOmsHub integration — the [FillEvent, OrderFilled] pair advances the FSM."""
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.events import OrderAccepted as OA, OrderRequest, OrderSubmitted as OS
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.order import OrderStatus
    from vike_trader_app.exec.risk import RiskGate, RiskLimits

    class _SyncClient:
        def __init__(self, bus):
            self._bus = bus

        def submit(self, request):
            self._bus.publish(OS(client_order_id=request.client_order_id))
            self._bus.publish(OA(client_order_id=request.client_order_id, venue_order_id="b-1"))

        def detach(self):
            pass

    bus = EventBus()
    hub = LiveOmsHub(bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
                     client=_SyncClient(bus), venue="binance", symbol="BTCUSDT")
    hub.submit_ticket(OrderRequest(client_order_id="coid-1", venue="binance", symbol="BTCUSDT",
                                   side=+1, qty=1.0, order_type="limit", price=65000.0))
    assert hub.registry["coid-1"].status is OrderStatus.ACCEPTED
    inner = _frame(x="TRADE", X="FILLED", c="coid-1", l="1.0", L="65000", t="T1", m=False)
    for ev in map_binance_private(_wrapped(inner), venue="binance", symbol="BTCUSDT"):
        bus.publish(ev)
    mo = hub.registry["coid-1"]
    assert mo.status is OrderStatus.FILLED       # OrderFilled wrap drove the FSM
    assert mo.filled_qty == pytest.approx(1.0)
