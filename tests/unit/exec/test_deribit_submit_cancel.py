"""submit() -> OrderSubmitted then OrderAccepted|OrderRejected over a scripted JSON-RPC transport.
cancel() -> private/cancel by recorded order_id; swallows not-found codes (10004, 11044, 10010, 11008),
re-raises other errors."""
import pytest

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.deribit.client import DeribitApiError, DeribitExecutionClient
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderRejected,
    OrderRequest,
)

_FILTERS = {"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
            "max_qty": 1000.0, "min_notional": 0.0}


def _seen(bus):
    out = []
    bus.subscribe(out.append)
    return out


def _client(bus, transport):
    return DeribitExecutionClient(bus, transport=transport, symbol="BTC-27JUN25-60000-C",
                                  filters=_FILTERS, currency="BTC")


def _req(side=+1, order_type="limit", price=0.05, coid="sess-0"):
    return OrderRequest(client_order_id=coid, venue="deribit", symbol="BTC-27JUN25-60000-C",
                        side=side, qty=1.0, order_type=order_type, price=price)


def test_submit_buy_acks_and_records_order_id():
    captured = {}

    def _transport(method, params):
        captured.update(method=method, params=params)
        return {"jsonrpc": "2.0", "id": 1,
                "result": {"order": {"order_id": "ETH-12345", "label": "sess-0",
                                     "order_state": "open"}, "trades": []}}

    bus = EventBus()
    seen = _seen(bus)
    c = _client(bus, _transport)
    c.submit(_req(side=+1))

    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderAccepted"]
    assert not any(isinstance(e, FillEvent) for e in seen)  # submit is ACK-only; fills via 6b WS
    acc = [e for e in seen if isinstance(e, OrderAccepted)][0]
    assert acc.venue_order_id == "ETH-12345"
    assert captured["method"] == "private/buy"
    assert captured["params"]["instrument_name"] == "BTC-27JUN25-60000-C"
    assert captured["params"]["post_only"] is False


def test_submit_sell_uses_private_sell():
    captured = {}

    def _transport(method, params):
        captured["method"] = method
        return {"id": 1, "result": {"order": {"order_id": "X"}, "trades": []}}

    c = _client(EventBus(), _transport)
    c.submit(_req(side=-1))
    assert captured["method"] == "private/sell"


def test_submit_rejected_on_jsonrpc_error():
    def _transport(method, params):
        return {"id": 1, "error": {"code": 10009, "message": "not_enough_funds"}}

    bus = EventBus()
    seen = _seen(bus)
    _client(bus, _transport).submit(_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderRejected"]
    rej = [e for e in seen if isinstance(e, OrderRejected)][0]
    assert "not_enough_funds" in rej.reason


def test_cancel_uses_recorded_order_id_and_swallows_not_found():
    sent = []

    def _transport(method, params):
        sent.append((method, params))
        if method == "private/buy":
            return {"id": 1, "result": {"order": {"order_id": "OID-9"}, "trades": []}}
        return {"id": 2, "error": {"code": 11044, "message": "not_open_order"}}  # already gone

    c = _client(EventBus(), _transport)
    c.submit(_req(coid="sess-7"))
    c.cancel("sess-7")  # must NOT raise (not-found swallowed)
    assert sent[-1] == ("private/cancel", {"order_id": "OID-9"})


def test_cancel_unknown_coid_is_noop():
    sent = []

    def _transport(method, params):
        sent.append(method)
        return {"id": 1, "result": {}}

    c = _client(EventBus(), _transport)
    c.cancel("never-submitted")          # unknown -> no transport call
    assert sent == []


def test_cancel_reraises_other_error():
    def _transport(method, params):
        if method == "private/buy":
            return {"id": 1, "result": {"order": {"order_id": "OID-1"}, "trades": []}}
        return {"id": 2, "error": {"code": 10028, "message": "too_many_requests"}}

    c = _client(EventBus(), _transport)
    c.submit(_req(coid="sess-1"))
    with pytest.raises(DeribitApiError) as ei:
        c.cancel("sess-1")
    assert ei.value.code == 10028


def test_cancel_swallows_already_filled_11008():
    """Critic fix: cancelling a just-FILLED order returns code 11008 -> must be swallowed."""
    sent = []

    def _transport(method, params):
        sent.append((method, params))
        if method == "private/buy":
            return {"id": 1, "result": {"order": {"order_id": "OID-filled"}, "trades": []}}
        return {"id": 2, "error": {"code": 11008, "message": "already_filled"}}

    c = _client(EventBus(), _transport)
    c.submit(_req(coid="sess-fill"))
    c.cancel("sess-fill")  # must NOT raise
    assert sent[-1] == ("private/cancel", {"order_id": "OID-filled"})


def test_cancel_swallows_already_closed_10010():
    """Critic fix: cancelling an already-closed order returns code 10010 -> must be swallowed."""
    sent = []

    def _transport(method, params):
        sent.append((method, params))
        if method == "private/buy":
            return {"id": 1, "result": {"order": {"order_id": "OID-closed"}, "trades": []}}
        return {"id": 2, "error": {"code": 10010, "message": "already_closed"}}

    c = _client(EventBus(), _transport)
    c.submit(_req(coid="sess-close"))
    c.cancel("sess-close")  # must NOT raise
    assert sent[-1] == ("private/cancel", {"order_id": "OID-closed"})
