"""connect() reconciles: base-asset free balance -> position; open orders -> ACCEPTED ManagedOrders."""

from vike_trader_app.exec.binance.client import BinanceSpotExecutionClient, ReconcileSnapshot
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.order import ManagedOrder, OrderStatus

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}


def _client(transport, public_transport=None):
    if public_transport is None:
        public_transport = lambda base, path, params: {"price": "65000.0"}  # noqa: E731
    return BinanceSpotExecutionClient(
        EventBus(), signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
        filters=_FILTERS, base_asset="BTC", transport=transport,
        public_transport=public_transport)


def test_connect_seeds_position_and_open_orders():
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v3/account":
            return {"balances": [{"asset": "BTC", "free": "0.5", "locked": "0.0"},
                                 {"asset": "USDT", "free": "5000", "locked": "0"}]}
        if path == "/api/v3/openOrders":
            return [{"clientOrderId": "prev-9", "orderId": 7,
                     "side": "SELL", "type": "LIMIT", "price": "70000", "origQty": "0.2",
                     "executedQty": "0.0"}]
        raise AssertionError(path)

    snap = _client(_transport).connect()
    assert isinstance(snap, ReconcileSnapshot)
    # size = free(0.5) + locked_sell(0.2 - 0.0 = 0.2) = 0.7
    assert snap.positions == (("BTCUSDT", 0.7),)
    assert len(snap.open_orders) == 1
    mo = snap.open_orders[0]
    assert isinstance(mo, ManagedOrder)
    assert mo.status is OrderStatus.ACCEPTED
    assert mo.client_order_id == "prev-9"
    assert mo.venue_order_id == "7"


def test_connect_with_no_balance_seeds_zero():
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v3/account":
            return {"balances": [{"asset": "USDT", "free": "5000", "locked": "0"}]}
        return []

    snap = _client(_transport).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.open_orders == ()


def test_connect_locked_sell_qty_included_in_seeded_size():
    """Resting SELL qty (minus already-executed) is added back to free balance."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v3/account":
            return {"balances": [{"asset": "BTC", "free": "1.0", "locked": "0.3"}]}
        if path == "/api/v3/openOrders":
            return [
                {"clientOrderId": "sell-1", "orderId": 11, "side": "SELL", "type": "LIMIT",
                 "price": "70000", "origQty": "0.3", "executedQty": "0.1"},
                {"clientOrderId": "buy-1", "orderId": 12, "side": "BUY", "type": "LIMIT",
                 "price": "60000", "origQty": "0.5", "executedQty": "0.0"},
            ]
        raise AssertionError(path)

    snap = _client(_transport).connect()
    # locked_sell = origQty(0.3) - executedQty(0.1) = 0.2; seeded = free(1.0) + 0.2 = 1.2
    assert snap.positions == (("BTCUSDT", 1.2),)


def test_connect_seeds_avg_px_from_mark_price():
    """avg_px in position_avg_px is the current market price, not 0.0."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v3/account":
            return {"balances": [{"asset": "BTC", "free": "0.5", "locked": "0.0"}]}
        if path == "/api/v3/openOrders":
            return []
        raise AssertionError(path)

    def _public(base, path, params):
        assert path == "/api/v3/ticker/price"
        assert params.get("symbol") == "BTCUSDT"
        return {"price": "68000.00"}

    snap = _client(_transport, public_transport=_public).connect()
    assert snap.position_avg_px == (("BTCUSDT", 68000.0),)
    # NOT the garbage default
    assert snap.position_avg_px[0][1] != 0.0
