"""Bybit connect() reconciles: wallet-balance UNIFIED coin[].availableToWithdraw -> free; open orders ->
ACCEPTED ManagedOrders with locked-SELL add-back; tickers lastPrice -> avg_px.

seeded_size = availableToWithdraw + locked_sell_qty = total held base asset.
Using walletBalance instead would double-count: walletBalance already includes locked-sell qty,
so walletBalance + locked_sell_qty > total. The live smoke (Task 11) is ground-truth for the
real demo wallet field shape."""

from __future__ import annotations

from vike_trader_app.exec.bybit.client import BybitSpotExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.order import ManagedOrder, OrderStatus

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}


def _client(transport, public_transport):
    return BybitSpotExecutionClient(
        EventBus(), signer=object(), rest_base_url="https://x", symbol="BTCUSDT",
        filters=_FILTERS, base_asset="BTC", transport=transport, public_transport=public_transport)


def test_connect_seeds_from_unified_wallet_and_open_orders():
    def _transport(base, path, method, params, signer, **kw):
        if path == "/v5/account/wallet-balance":
            assert params == {"accountType": "UNIFIED"}
            return {"retCode": 0, "result": {"list": [
                {"accountType": "UNIFIED", "coin": [
                    # walletBalance=0.5 is TOTAL (free 0.35 + locked-sell 0.15);
                    # availableToWithdraw=0.35 is the FREE portion (excludes locked sell qty).
                    # seeded_size = availableToWithdraw(0.35) + locked_sell(0.15) = 0.5 (correct total).
                    {"coin": "BTC", "walletBalance": "0.5", "availableToWithdraw": "0.35"},
                    {"coin": "USDT", "walletBalance": "5000", "availableToWithdraw": "5000"},
                ]}]}}
        if path == "/v5/order/realtime":
            assert params == {"category": "spot", "symbol": "BTCUSDT"}
            return {"retCode": 0, "result": {"list": [
                {"orderLinkId": "prev-9", "orderId": "7", "side": "Sell", "orderType": "Limit",
                 "price": "70000", "qty": "0.2", "cumExecQty": "0.05"}]}}
        raise AssertionError(path)

    def _public(base, path, params):
        assert path == "/v5/market/tickers"
        return {"retCode": 0, "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "68000.0"}]}}

    snap = _client(_transport, _public).connect()
    assert isinstance(snap, ReconcileSnapshot)
    # seeded = availableToWithdraw(0.35) + locked_sell(0.2 - 0.05 = 0.15) = 0.5 (= walletBalance total, correct)
    assert snap.positions == (("BTCUSDT", 0.5),)
    assert snap.position_avg_px == (("BTCUSDT", 68000.0),)
    assert len(snap.open_orders) == 1
    mo = snap.open_orders[0]
    assert isinstance(mo, ManagedOrder)
    assert mo.status is OrderStatus.ACCEPTED
    assert mo.client_order_id == "prev-9"
    assert mo.venue_order_id == "7"


def test_connect_no_base_balance_seeds_zero():
    def _transport(base, path, method, params, signer, **kw):
        if path == "/v5/account/wallet-balance":
            return {"retCode": 0, "result": {"list": [
                {"coin": [{"coin": "USDT", "walletBalance": "5000"}]}]}}
        if path == "/v5/order/realtime":
            return {"retCode": 0, "result": {"list": []}}
        raise AssertionError(path)

    def _public(base, path, params):
        return {"retCode": 0, "result": {"list": [{"lastPrice": "68000.0"}]}}

    snap = _client(_transport, _public).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.open_orders == ()
