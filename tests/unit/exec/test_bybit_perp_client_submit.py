"""Tests for BybitPerpExecutionClient submit / set-leverage (offline, fake transport)."""
from __future__ import annotations

import pytest

from vike_trader_app.exec.bybit.perp_client import BybitPerpExecutionClient
from vike_trader_app.exec.bybit.transport import BybitApiError
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import OrderAccepted, OrderRejected, OrderRequest

_F = {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001, "max_qty": 9e3, "min_notional": 5.0}


def _client(transport):
    return BybitPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                    symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                    transport=transport, public_transport=lambda *a, **k: {})


def test_market_buy_linear_params_no_market_unit():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap["params"] = params; return {"retCode": 0, "result": {"orderId": "1"}}
    req = OrderRequest(client_order_id="p-0", venue="bybit", symbol="BTCUSDT",
                       side=+1, qty=0.01, order_type="market", price=None)
    _client(t).submit(req)
    p = cap["params"]
    assert p["category"] == "linear"
    assert p["positionIdx"] == 0
    assert p["side"] == "Buy"
    assert p["orderType"] == "Market"
    assert p["qty"] == "0.010"
    assert "marketUnit" not in p              # DROPPED for linear
    assert p["reduceOnly"] is False


def test_reduce_only_sell_sets_flag():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap["params"] = params; return {"retCode": 0, "result": {"orderId": "1"}}
    req = OrderRequest(client_order_id="p-1", venue="bybit", symbol="BTCUSDT",
                       side=-1, qty=0.01, order_type="market", price=None, reduce_only=True)
    _client(t).submit(req)
    assert cap["params"]["reduceOnly"] is True
    assert cap["params"]["side"] == "Sell"


def test_set_leverage_swallows_110043():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap.update(path=path, params=params)
        return {"retCode": 110043, "retMsg": "leverage not modified"}
    _client(t).set_leverage()                  # must NOT raise
    assert cap["path"] == "/v5/position/set-leverage"
    assert cap["params"]["category"] == "linear"
    assert cap["params"]["buyLeverage"] == cap["params"]["sellLeverage"]  # one-way buy==sell


def test_set_leverage_reraises_other_errors():
    def t(*a, **k): return {"retCode": 10004, "retMsg": "sign"}
    with pytest.raises(BybitApiError):
        _client(t).set_leverage()


def test_limit_buy_carries_price_and_tif():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap["params"] = params; return {"retCode": 0, "result": {"orderId": "2"}}
    req = OrderRequest(client_order_id="p-2", venue="bybit", symbol="BTCUSDT",
                       side=+1, qty=0.05, order_type="limit", price=65000.0)
    _client(t).submit(req)
    p = cap["params"]
    assert p["orderType"] == "Limit"
    assert p["timeInForce"] == "GTC"
    assert p["price"] == "65000.0"
    assert p["reduceOnly"] is False


def test_cancel_uses_linear_category():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap["params"] = params; return {"retCode": 0, "result": {}}
    _client(t).cancel("p-99")
    assert cap["params"]["category"] == "linear"
    assert cap["params"]["orderLinkId"] == "p-99"
