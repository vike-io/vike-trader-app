"""Bybit submit() is ACK-only: OrderSubmitted->OrderAccepted on retCode 0, OrderRejected on retCode!=0.
Market-Buy carries marketUnit=baseCoin. cancel() POSTs /v5/order/cancel, swallows 110001."""

import pytest

from vike_trader_app.exec.bybit.client import BybitSpotExecutionClient
from vike_trader_app.exec.bybit.transport import BybitApiError
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderRejected,
    OrderRequest,
)

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}


def _seen(bus):
    out = []
    bus.subscribe(out.append)
    return out


def _client(transport):
    return BybitSpotExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                    symbol="BTCUSDT", filters=_FILTERS, base_asset="BTC",
                                    transport=transport, public_transport=lambda *a, **k: {})


def _limit_req(coid="sess-0"):
    return OrderRequest(client_order_id=coid, venue="bybit", symbol="BTCUSDT",
                        side=+1, qty=0.30000000000000004, order_type="limit", price=65000.0)


def test_submit_limit_buy_builds_params_and_acks():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured.update(params=params, path=path, method=method)
        return {"retCode": 0, "retMsg": "OK", "result": {"orderId": "abc123", "orderLinkId": "sess-0"}}

    bus = EventBus()
    seen = _seen(bus)
    client = BybitSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_FILTERS,
                                      transport=_transport, public_transport=lambda *a, **k: {})
    client.submit(_limit_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderAccepted"]
    assert not any(isinstance(e, FillEvent) for e in seen)
    acc = [e for e in seen if isinstance(e, OrderAccepted)][0]
    assert acc.venue_order_id == "abc123"
    p = captured["params"]
    assert p["category"] == "spot"
    assert p["symbol"] == "BTCUSDT"
    assert p["side"] == "Buy"
    assert p["orderType"] == "Limit"
    assert p["qty"] == "0.300"
    assert p["price"] == "65000.00"
    assert p["orderLinkId"] == "sess-0"
    assert p["timeInForce"] == "GTC"
    assert "marketUnit" not in p  # only Market-Buy sets it
    assert captured["path"] == "/v5/order/create"
    assert captured["method"] == "POST"


def test_market_buy_sets_market_unit_base_coin():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured["params"] = params
        return {"retCode": 0, "result": {"orderId": "1"}}

    req = OrderRequest(client_order_id="m-0", venue="bybit", symbol="BTCUSDT",
                       side=+1, qty=0.01, order_type="market", price=None)
    _client(_transport).submit(req)
    assert captured["params"]["orderType"] == "Market"
    assert captured["params"]["marketUnit"] == "baseCoin"
    assert "price" not in captured["params"]


def test_market_sell_does_not_set_market_unit():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured["params"] = params
        return {"retCode": 0, "result": {"orderId": "1"}}

    req = OrderRequest(client_order_id="m-1", venue="bybit", symbol="BTCUSDT",
                       side=-1, qty=0.01, order_type="market", price=None)
    _client(_transport).submit(req)
    assert "marketUnit" not in captured["params"]
    assert captured["params"]["side"] == "Sell"


def test_submit_rejects_on_ret_code_error():
    def _transport(*a, **kw):
        return {"retCode": 170131, "retMsg": "Insufficient balance", "result": {}}

    bus = EventBus()
    seen = _seen(bus)
    client = BybitSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_FILTERS,
                                      transport=_transport, public_transport=lambda *a, **k: {})
    client.submit(_limit_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderRejected"]
    rej = [e for e in seen if isinstance(e, OrderRejected)][0]
    assert "Insufficient balance" in rej.reason


def test_cancel_posts_order_link_id_and_swallows_110001():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured.update(path=path, method=method, params=params)
        return {"retCode": 110001, "retMsg": "order not exists or too late to cancel"}

    _client(_transport).cancel("sess-0")  # must not raise
    assert captured["path"] == "/v5/order/cancel"
    assert captured["method"] == "POST"
    assert captured["params"]["category"] == "spot"
    assert captured["params"]["orderLinkId"] == "sess-0"


def test_cancel_reraises_non_not_found():
    def _transport(*a, **kw):
        return {"retCode": 10004, "retMsg": "error sign"}

    with pytest.raises(BybitApiError) as ei:
        _client(_transport).cancel("sess-0")
    assert ei.value.code == 10004
