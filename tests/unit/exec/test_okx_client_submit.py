"""OKX submit() is ACK-only: OrderSubmitted->OrderAccepted on code=="0" and sCode=="0",
OrderRejected on top-level code!="0" OR per-order sCode!="0" with top code=="0".
Market-Buy carries tgtCcy=base_ccy. cancel() POSTs /api/v5/trade/cancel-order, swallows 51400/51401/51402."""

import pytest

from vike_trader_app.exec.okx.client import OKXSpotExecutionClient
from vike_trader_app.exec.okx.transport import OKXApiError
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


def _client(transport, td_mode="cash"):
    c = OKXSpotExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                               symbol="BTC-USDT", filters=_FILTERS, base_asset="BTC",
                               transport=transport, public_transport=lambda *a, **k: {})
    c._td_mode = td_mode  # pre-seed to avoid a config call in unit tests
    return c


def _limit_req(coid="sess-0"):
    return OrderRequest(client_order_id=coid, venue="okx", symbol="BTC-USDT",
                        side=+1, qty=0.30000000000000004, order_type="limit", price=65000.0)


def test_submit_limit_buy_builds_params_and_acks():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured.update(params=params, path=path, method=method)
        return {"code": "0", "data": [{"ordId": "abc123", "clOrdId": "sess-0", "sCode": "0", "sMsg": ""}]}

    bus = EventBus()
    seen = _seen(bus)
    client = OKXSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                    symbol="BTC-USDT", filters=_FILTERS,
                                    transport=_transport, public_transport=lambda *a, **k: {})
    client._td_mode = "cash"  # pre-seed; tdMode auto-detection tested in test_okx_tdmode.py
    client.submit(_limit_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderAccepted"]
    assert not any(isinstance(e, FillEvent) for e in seen)
    acc = [e for e in seen if isinstance(e, OrderAccepted)][0]
    assert acc.venue_order_id == "abc123"
    p = captured["params"]
    assert p["instId"] == "BTC-USDT"
    assert p["tdMode"] == "cash"
    assert p["side"] == "buy"
    assert p["ordType"] == "limit"
    assert p["sz"] == "0.300"
    assert p["px"] == "65000.00"
    assert p["clOrdId"] == "sess-0"
    assert "tgtCcy" not in p  # only Market-Buy sets it
    assert captured["path"] == "/api/v5/trade/order"
    assert captured["method"] == "POST"


def test_market_buy_sets_tgt_ccy_base():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured["params"] = params
        return {"code": "0", "data": [{"ordId": "1", "sCode": "0", "sMsg": ""}]}

    req = OrderRequest(client_order_id="m-0", venue="okx", symbol="BTC-USDT",
                       side=+1, qty=0.01, order_type="market", price=None)
    _client(_transport).submit(req)
    assert captured["params"]["ordType"] == "market"
    assert captured["params"]["tgtCcy"] == "base_ccy"
    assert "px" not in captured["params"]


def test_market_sell_omits_tgt_ccy():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured["params"] = params
        return {"code": "0", "data": [{"ordId": "1", "sCode": "0", "sMsg": ""}]}

    req = OrderRequest(client_order_id="m-1", venue="okx", symbol="BTC-USDT",
                       side=-1, qty=0.01, order_type="market", price=None)
    _client(_transport).submit(req)
    assert "tgtCcy" not in captured["params"]
    assert captured["params"]["side"] == "sell"


def test_submit_rejects_on_top_level_code():
    def _transport(*a, **kw):
        return {"code": "1", "msg": "all orders failed",
                "data": [{"sCode": "51008", "sMsg": "Insufficient balance"}]}

    bus = EventBus()
    seen = _seen(bus)
    client = OKXSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                    symbol="BTC-USDT", filters=_FILTERS,
                                    transport=_transport, public_transport=lambda *a, **k: {})
    client._td_mode = "cash"
    client.submit(_limit_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderRejected"]
    rej = [e for e in seen if isinstance(e, OrderRejected)][0]
    assert "Insufficient balance" in rej.reason


def test_submit_rejects_on_per_order_scode_with_top_code_zero():
    def _transport(*a, **kw):
        return {"code": "0", "data": [{"ordId": "", "sCode": "51000", "sMsg": "Parameter error"}]}

    bus = EventBus()
    seen = _seen(bus)
    client = OKXSpotExecutionClient(bus, signer=object(), rest_base_url="https://x",
                                    symbol="BTC-USDT", filters=_FILTERS,
                                    transport=_transport, public_transport=lambda *a, **k: {})
    client._td_mode = "cash"
    client.submit(_limit_req())
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderRejected"]
    rej = [e for e in seen if isinstance(e, OrderRejected)][0]
    assert "Parameter error" in rej.reason


def test_cancel_posts_clordid_and_swallows_51400():
    captured = {}

    def _transport(base, path, method, params, signer, **kw):
        captured.update(path=path, method=method, params=params)
        return {"code": "1", "data": [{"clOrdId": "sess-0", "sCode": "51400", "sMsg": "...does not exist."}]}

    _client(_transport).cancel("sess-0")  # must not raise
    assert captured["path"] == "/api/v5/trade/cancel-order"
    assert captured["method"] == "POST"
    assert captured["params"] == {"instId": "BTC-USDT", "clOrdId": "sess-0"}


def test_cancel_reraises_non_not_found():
    def _transport(*a, **kw):
        return {"code": "1", "data": [{"sCode": "50011", "sMsg": "rate limit"}]}

    with pytest.raises(OKXApiError) as ei:
        _client(_transport).cancel("sess-0")
    assert ei.value.code == 50011
