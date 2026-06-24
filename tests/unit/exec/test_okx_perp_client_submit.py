"""Tests for OKXPerpExecutionClient submit / set-leverage (offline, fake transport)."""
from __future__ import annotations

import pytest

from vike_trader_app.exec.okx.perp_client import OKXPerpExecutionClient
from vike_trader_app.exec.okx.transport import OKXApiError
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import OrderRequest

_F = {"tick_size": 0.1, "step_size": 0.1, "min_qty": 0.1, "max_qty": 1e4, "min_notional": 0.0}


def _client(transport):
    return OKXPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                  symbol="BTC-USDT-SWAP", filters=_F, base_asset="BTC",
                                  ct_val=0.01, leverage=3.0,
                                  transport=transport, public_transport=lambda *a, **k: {})


def test_market_buy_sends_contracts_not_base():
    """0.05 BTC / ct_val=0.01 = 5 contracts. tgtCcy must NOT appear (SWAP drop)."""
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"code": "0", "data": [{"ordId": "1", "sCode": "0"}]}

    req = OrderRequest(client_order_id="p-0", venue="okx", symbol="BTC-USDT-SWAP",
                       side=+1, qty=0.05, order_type="market", price=None)
    _client(t).submit(req)
    p = cap["params"]
    assert p["sz"] == "5.0"                     # contracts, not base
    assert p["instId"] == "BTC-USDT-SWAP"
    assert p["tdMode"] == "cross"
    assert p["side"] == "buy"
    assert p["ordType"] == "market"
    assert p["posSide"] == "net"
    assert p["reduceOnly"] is False
    assert "tgtCcy" not in p                    # CRITICAL: SWAP drop


def test_base_to_contracts_floors_to_lot_not_rounded_to_whole():
    """qty=0.054, ct_val=0.01 → 5.4 contracts, FLOORED to lotSz 0.1 → '5.4'.

    OKX SWAP allows FRACTIONAL contracts — base/ct_val must NOT be rounded to a whole contract
    first (that would floor 5.4 → 5.0, and any sub-0.5-contract order → 0)."""
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"code": "0", "data": [{"ordId": "2", "sCode": "0"}]}

    req = OrderRequest(client_order_id="p-1", venue="okx", symbol="BTC-USDT-SWAP",
                       side=+1, qty=0.054, order_type="market", price=None)
    _client(t).submit(req)
    assert cap["params"]["sz"] == "5.4"


def test_sub_one_contract_order_not_floored_to_zero():
    """A small order (0.0002 BTC = 0.02 contracts) with lotSz 0.01 sizes to '0.02', NOT '0'.

    The old round(base/ct_val)-to-int floored every sub-0.5-contract order to zero — but the live
    SWAP minimum is lotSz contracts (~$10), not 1 whole contract (~$1000). RED-proven by the live smoke."""
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"code": "0", "data": [{"ordId": "4", "sCode": "0"}]}

    f = {"tick_size": 0.1, "step_size": 0.01, "min_qty": 0.01, "max_qty": 1e4, "min_notional": 0.0}
    client = OKXPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                    symbol="BTC-USDT-SWAP", filters=f, base_asset="BTC",
                                    ct_val=0.01, leverage=3.0,
                                    transport=t, public_transport=lambda *a, **k: {})
    req = OrderRequest(client_order_id="p-3", venue="okx", symbol="BTC-USDT-SWAP",
                       side=+1, qty=0.0002, order_type="market", price=None)
    client.submit(req)
    assert cap["params"]["sz"] == "0.02"


def test_reduce_only_sell_sets_flag_and_posside_net():
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"code": "0", "data": [{"ordId": "3", "sCode": "0"}]}

    req = OrderRequest(client_order_id="p-2", venue="okx", symbol="BTC-USDT-SWAP",
                       side=-1, qty=0.01, order_type="market", price=None, reduce_only=True)
    _client(t).submit(req)
    p = cap["params"]
    assert p["reduceOnly"] is True
    assert p["side"] == "sell"
    assert p["posSide"] == "net"


def test_limit_buy_carries_px_and_contracts():
    """order_type=limit, qty=0.03 → 3 contracts; px included."""
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"code": "0", "data": [{"ordId": "4", "sCode": "0"}]}

    req = OrderRequest(client_order_id="p-3", venue="okx", symbol="BTC-USDT-SWAP",
                       side=+1, qty=0.03, order_type="limit", price=65000.0)
    _client(t).submit(req)
    p = cap["params"]
    assert p["ordType"] == "limit"
    assert p["px"] == "65000.0"
    assert p["sz"] == "3.0"                     # 0.03 / 0.01 = 3 contracts


def test_set_leverage_posts_cross_mode():
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap.update(path=path, params=params)
        return {"code": "0", "data": [{"lever": "3", "mgnMode": "cross",
                                       "instId": "BTC-USDT-SWAP"}]}

    _client(t).set_leverage()               # must NOT raise
    assert cap["path"] == "/api/v5/account/set-leverage"
    assert cap["params"]["mgnMode"] == "cross"
    assert cap["params"]["lever"] == "3"
    assert cap["params"]["instId"] == "BTC-USDT-SWAP"


def test_set_leverage_reraises_on_error_code():
    """Any non-'0' OKX code must re-raise — EMPTY swallow set, nothing is silently swallowed."""
    def t(base, path, method, params, signer, **k):
        return {"code": "1", "data": [{"sCode": "50011", "sMsg": "rate limit"}]}

    with pytest.raises(OKXApiError):
        _client(t).set_leverage()


def test_cancel_uses_swap_instid():
    cap = {}

    def t(base, path, method, params, signer, **k):
        cap["params"] = params
        return {"code": "0", "data": [{"sCode": "0"}]}

    _client(t).cancel("p-9")
    assert cap["params"]["instId"] == "BTC-USDT-SWAP"
    assert cap["params"]["clOrdId"] == "p-9"
