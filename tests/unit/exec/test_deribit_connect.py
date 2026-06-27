"""6d: connect() calls private/get_positions + private/get_open_orders_by_instrument over the injected
transport and returns a POPULATED ReconcileSnapshot (the 6a empty contract is gone). Fake transport
dispatches by method name and returns scripted JSON-RPC {"id","result"} dicts."""
import pytest

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.deribit.client import DeribitApiError, DeribitExecutionClient
from vike_trader_app.exec.order import OrderStatus

_FILTERS = {"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
            "max_qty": 1000.0, "min_notional": 0.0}
_SYM = "BTC-27JUN26-60000-C"


def _client(transport):
    return DeribitExecutionClient(EventBus(), transport=transport, symbol=_SYM,
                                  filters=_FILTERS, currency="BTC")


def test_connect_seeds_position_and_open_orders():
    def t(method, params):
        if method == "private/get_positions":
            assert params == {"currency": "BTC", "kind": "option"}
            return {"id": 1, "result": [
                {"instrument_name": _SYM, "direction": "buy", "size": 2.0,
                 "average_price": 0.05, "mark_price": 0.052, "kind": "option"}]}
        if method == "private/get_open_orders_by_instrument":
            assert params == {"instrument_name": _SYM, "type": "all"}
            return {"id": 2, "result": [
                {"order_id": "146062", "instrument_name": _SYM, "direction": "sell",
                 "amount": 1.0, "filled_amount": 0.0, "average_price": 0.0, "price": 0.07,
                 "order_state": "open", "order_type": "limit", "label": "vike-7"}]}
        raise AssertionError(method)

    snap = _client(t).connect()
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == ((_SYM, 2.0),)
    assert snap.position_avg_px == ((_SYM, 0.05),)
    assert snap.position_mark_px == ((_SYM, 0.052),)
    assert snap.position_sides == ()
    assert len(snap.open_orders) == 1
    mo = snap.open_orders[0]
    assert mo.status is OrderStatus.ACCEPTED
    assert mo.client_order_id == "vike-7"
    assert mo.venue_order_id == "146062"


def test_connect_flat_when_no_position():
    def t(method, params):
        if method == "private/get_positions":
            return {"id": 1, "result": []}
        return {"id": 2, "result": []}

    snap = _client(t).connect()
    assert snap.positions == ((_SYM, 0.0),)
    assert snap.position_avg_px == ((_SYM, 0.0),)
    assert snap.open_orders == ()


def test_connect_raises_on_positions_error():
    def t(method, params):
        if method == "private/get_positions":
            return {"id": 1, "error": {"code": 13009, "message": "unauthorized"}}
        return {"id": 2, "result": []}

    with pytest.raises(DeribitApiError) as exc:
        _client(t).connect()
    assert exc.value.code == 13009
    # secrets never logged: the error carries only code+message
    assert "unauthorized" in str(exc.value)


def test_connect_raises_on_orders_error():
    def t(method, params):
        if method == "private/get_positions":
            return {"id": 1, "result": []}
        return {"id": 2, "error": {"code": 11094, "message": "bad request"}}

    with pytest.raises(DeribitApiError):
        _client(t).connect()


def test_connect_through_real_apply_snapshot_seeds_account_and_registry():
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.live_oms import LiveOmsHub

    def t(method, params):
        if method == "private/get_positions":
            return {"id": 1, "result": [
                {"instrument_name": _SYM, "direction": "buy", "size": 3.0,
                 "average_price": 0.05, "mark_price": 0.06}]}
        return {"id": 2, "result": [
            {"order_id": "500", "instrument_name": _SYM, "direction": "buy",
             "amount": 1.0, "filled_amount": 0.0, "average_price": 0.0, "price": 0.04,
             "order_state": "open", "order_type": "limit", "label": "vike-1"}]}

    bus = EventBus()
    client = DeribitExecutionClient(bus, transport=t, symbol=_SYM, filters=_FILTERS, currency="BTC")
    hub = LiveOmsHub(bus=bus, account=Account(), gate=object(), client=client,
                     venue="deribit", symbol=_SYM)
    hub.apply_snapshot(client.connect())   # must NOT trip the sym == hub.symbol assert

    pos = hub.account.positions[("deribit", _SYM, "BOTH")]
    assert pos["size"] == 3.0
    assert pos["avg_px"] == 0.05
    assert hub.account.marks[("deribit", _SYM)] == 0.06
    assert "vike-1" in hub.registry
    assert hub.registry["vike-1"].status is OrderStatus.ACCEPTED
