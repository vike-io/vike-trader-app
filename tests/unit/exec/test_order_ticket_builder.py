import pytest

from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.order_ticket import build_order_request


def _build(**over):
    kw = dict(hub_venue="binance", hub_symbol="BTCUSDT", side=1, qty=0.01,
              order_type="market", price=None, reduce_only=False,
              client_order_id="sess0", now_ms=1234)
    kw.update(over)
    return build_order_request(**kw)


def test_builds_order_request_from_hub_context():
    req = _build()
    assert isinstance(req, OrderRequest)
    assert req.venue == "binance"
    assert req.symbol == "BTCUSDT"
    assert req.side == 1
    assert req.qty == 0.01
    assert req.order_type == "market"
    assert req.price is None
    assert req.reduce_only is False
    assert req.client_order_id == "sess0"
    assert req.ts == 1234


def test_symbol_is_hub_symbol_not_chart_symbol():
    # OKX perp: hub.symbol == 'BTC-USDT-SWAP' diverges from the chart 'BTCUSDT'.
    req = _build(hub_venue="okx", hub_symbol="BTC-USDT-SWAP")
    assert req.symbol == "BTC-USDT-SWAP"   # the venue/client symbol, NEVER the chart symbol


def test_buy_is_plus_one_sell_is_minus_one():
    assert _build(side=1).side == 1
    assert _build(side=-1).side == -1


def test_market_order_carries_no_price_even_if_supplied():
    # A market order must value at the mark inside the gate (price=None); a stray price is dropped.
    req = _build(order_type="market", price=999.0)
    assert req.price is None


def test_limit_order_carries_price():
    req = _build(order_type="limit", price=65000.0)
    assert req.order_type == "limit"
    assert req.price == 65000.0


def test_limit_order_without_price_raises():
    with pytest.raises(ValueError):
        _build(order_type="limit", price=None)


def test_reduce_only_passthrough():
    assert _build(reduce_only=True).reduce_only is True


def test_invalid_side_raises():
    with pytest.raises(ValueError):
        _build(side=0)
    with pytest.raises(ValueError):
        _build(side=2)


def test_non_positive_qty_raises():
    with pytest.raises(ValueError):
        _build(qty=0.0)
    with pytest.raises(ValueError):
        _build(qty=-1.0)


def test_unknown_order_type_raises():
    with pytest.raises(ValueError):
        _build(order_type="stop")   # MVP is market+limit only


def test_client_order_id_and_ts_are_passthrough():
    req = _build(client_order_id="abc123", now_ms=999)
    assert req.client_order_id == "abc123"
    assert req.ts == 999


def test_build_close_request_long_sells_full_size_market():
    from vike_trader_app.exec.order_ticket import build_close_request
    req = build_close_request(hub_venue="binance", hub_symbol="BTCUSDT", held_size=4.0,
                              reduce_only=True, client_order_id="c-1", now_ms=123)
    assert (req.side, req.qty, req.order_type, req.reduce_only, req.price) == (-1, 4.0, "market", True, None)
    assert (req.venue, req.symbol, req.client_order_id, req.ts) == ("binance", "BTCUSDT", "c-1", 123)


def test_build_close_request_short_buys_full_size():
    from vike_trader_app.exec.order_ticket import build_close_request
    req = build_close_request(hub_venue="binance", hub_symbol="BTCUSDT", held_size=-3.0,
                              reduce_only=False, client_order_id="c-2", now_ms=9)
    assert (req.side, req.qty, req.reduce_only) == (+1, 3.0, False)
