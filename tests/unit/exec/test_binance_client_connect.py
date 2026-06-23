"""connect() reconciles: base-asset free balance -> position; open orders -> ACCEPTED ManagedOrders."""

from vike_trader_app.exec.binance.client import BinanceSpotExecutionClient, ReconcileSnapshot
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.order import ManagedOrder, OrderStatus

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}


def _client(transport):
    return BinanceSpotExecutionClient(
        EventBus(), signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
        filters=_FILTERS, base_asset="BTC", transport=transport)


def test_connect_seeds_position_and_open_orders():
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v3/account":
            return {"balances": [{"asset": "BTC", "free": "0.5", "locked": "0.0"},
                                 {"asset": "USDT", "free": "5000", "locked": "0"}]}
        if path == "/api/v3/openOrders":
            return [{"clientOrderId": "prev-9", "orderId": 7,
                     "side": "SELL", "type": "LIMIT", "price": "70000", "origQty": "0.2"}]
        raise AssertionError(path)

    snap = _client(_transport).connect()
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTCUSDT", 0.5),)
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
