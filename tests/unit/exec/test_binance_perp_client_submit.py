from __future__ import annotations
import pytest
from vike_trader_app.exec.binance.perp_client import BinancePerpExecutionClient
from vike_trader_app.exec.binance.transport import BinanceApiError
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import OrderRequest

_F = {"tick_size": 0.10, "step_size": 0.001, "min_qty": 0.001, "max_qty": 120.0, "min_notional": 100.0}

def _client(transport):
    return BinancePerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                      leverage=3.0, transport=transport,
                                      public_transport=lambda *a, **k: {})

def test_market_buy_base_qty_positionside_both_reduceonly_string_false():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap.update(path=path, method=method, params=params)
        return {"orderId": 1}
    req = OrderRequest(client_order_id="p-0", venue="binance", symbol="BTCUSDT",
                       side=+1, qty=0.0123, order_type="market", price=None)
    _client(t).submit(req)
    p = cap["params"]
    assert cap["path"] == "/fapi/v1/order" and cap["method"] == "POST"
    assert p["symbol"] == "BTCUSDT" and p["side"] == "BUY" and p["type"] == "MARKET"
    assert p["quantity"] == "0.012"                 # base qty floored to stepSize 0.001 — NO contracts
    assert p["positionSide"] == "BOTH"
    assert p["reduceOnly"] == "false"               # STRING, not bool False
    assert p["newClientOrderId"] == "p-0"
    assert p["newOrderRespType"] == "ACK"
    assert "timeInForce" not in p and "price" not in p

def test_reduce_only_sell_string_true():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"orderId": 2}
    req = OrderRequest(client_order_id="p-1", venue="binance", symbol="BTCUSDT",
                       side=-1, qty=0.01, order_type="market", price=None, reduce_only=True)
    _client(t).submit(req)
    assert cap["params"]["side"] == "SELL"
    assert cap["params"]["reduceOnly"] == "true"    # STRING true
    assert cap["params"]["positionSide"] == "BOTH"

def test_limit_buy_carries_tif_and_price():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"orderId": 3}
    req = OrderRequest(client_order_id="p-2", venue="binance", symbol="BTCUSDT",
                       side=+1, qty=0.005, order_type="limit", price=65000.07)
    _client(t).submit(req)
    p = cap["params"]
    assert p["type"] == "LIMIT" and p["timeInForce"] == "GTC"
    assert p["price"] == "65000.0"                  # floored to tickSize 0.10 → 1 decimal place
    assert p["quantity"] == "0.005"

def test_cancel_uses_orig_client_order_id():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap.update(path=path, method=method, params=params)
        return {}
    _client(t).cancel("p-9")
    assert cap["path"] == "/fapi/v1/order" and cap["method"] == "DELETE"
    assert cap["params"] == {"symbol": "BTCUSDT", "origClientOrderId": "p-9"}

def test_cancel_swallows_2011_not_found():
    def t(base, path, method, params, signer, **k):
        raise BinanceApiError(-2011, "Unknown order sent.")
    _client(t).cancel("p-x")            # inherited is_order_not_found(-2011) -> swallowed, no raise

def test_set_leverage_posts_int_leverage_no_swallow():
    cap = {}
    def t(base, path, method, params, signer, **k):
        cap.update(path=path, method=method, params=params)
        return {"leverage": 3, "symbol": "BTCUSDT", "maxNotionalValue": "1000000"}
    _client(t).set_leverage()
    assert cap["path"] == "/fapi/v1/leverage" and cap["method"] == "POST"
    assert cap["params"] == {"symbol": "BTCUSDT", "leverage": "3"}   # str(int(3.0)) == "3"

def test_set_leverage_reraises_on_error():
    def t(base, path, method, params, signer, **k):
        raise BinanceApiError(-4028, "Leverage 3 is not valid")
    with pytest.raises(BinanceApiError):
        _client(t).set_leverage()       # NO benign swallow — Binance is idempotent 200 on success
