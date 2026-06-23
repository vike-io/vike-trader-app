"""Bybit connect() reconciles: wallet-balance UNIFIED coin[].walletBalance (TOTAL) -> free;
open orders -> ACCEPTED ManagedOrders; tickers lastPrice -> avg_px.

seeded_size = walletBalance (already the total: free + locked-sell).
BALANCE_IS_TOTAL=True tells the base NOT to add locked_sell_qty on top.
availableToWithdraw is deprecated/empty ("") for UNIFIED — must never be used.

Ground truth from api-demo.bybit.com: walletBalance="1", availableToWithdraw="".
"""

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


def test_connect_seeds_from_wallet_balance_total():
    """walletBalance is the TOTAL (free + locked-sell); seed must equal walletBalance exactly.
    An open SELL order must NOT be double-added (BALANCE_IS_TOTAL=True path)."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/v5/account/wallet-balance":
            assert params == {"accountType": "UNIFIED"}
            return {"retCode": 0, "result": {"list": [
                {"accountType": "UNIFIED", "coin": [
                    # Real demo shape: walletBalance="1", availableToWithdraw="" (deprecated/empty).
                    # seeded_size must equal walletBalance=1.0 — locked-sell must NOT be added.
                    {"coin": "BTC", "walletBalance": "1.0", "availableToWithdraw": ""},
                    {"coin": "USDT", "walletBalance": "5000", "availableToWithdraw": ""},
                ]}]}}
        if path == "/v5/order/realtime":
            assert params == {"category": "spot", "symbol": "BTCUSDT", "limit": 50}
            return {"retCode": 0, "result": {"list": [
                # Open SELL 0.15 BTC — must NOT be added to the seed (walletBalance already total).
                {"orderLinkId": "prev-9", "orderId": "7", "side": "Sell", "orderType": "Limit",
                 "price": "70000", "qty": "0.15", "cumExecQty": "0.0"}]}}
        raise AssertionError(path)

    def _public(base, path, params):
        assert path == "/v5/market/tickers"
        return {"retCode": 0, "result": {"list": [{"symbol": "BTCUSDT", "lastPrice": "68000.0"}]}}

    snap = _client(_transport, _public).connect()
    assert isinstance(snap, ReconcileSnapshot)
    # seed = walletBalance total (1.0); locked-sell NOT added (BALANCE_IS_TOTAL=True)
    assert snap.positions == (("BTCUSDT", 1.0),)
    assert snap.position_avg_px == (("BTCUSDT", 68000.0),)
    assert len(snap.open_orders) == 1
    mo = snap.open_orders[0]
    assert isinstance(mo, ManagedOrder)
    assert mo.status is OrderStatus.ACCEPTED
    assert mo.client_order_id == "prev-9"
    assert mo.venue_order_id == "7"


def test_available_to_withdraw_empty_string_does_not_seed_zero():
    """Regression: availableToWithdraw='' must NOT be used — it's deprecated/empty for UNIFIED.
    walletBalance=1.0 must be the seed regardless of availableToWithdraw value."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/v5/account/wallet-balance":
            return {"retCode": 0, "result": {"list": [
                {"accountType": "UNIFIED", "coin": [
                    # This is the EXACT real demo shape that triggered the bug:
                    # availableToWithdraw="" (deprecated empty) must be ignored entirely.
                    {"coin": "BTC", "walletBalance": "1", "availableToWithdraw": ""},
                ]}]}}
        if path == "/v5/order/realtime":
            return {"retCode": 0, "result": {"list": []}}
        raise AssertionError(path)

    def _public(base, path, params):
        return {"retCode": 0, "result": {"list": [{"lastPrice": "68000.0"}]}}

    snap = _client(_transport, _public).connect()
    # Must be 1.0, NOT 0.0 (which availableToWithdraw="" would have caused previously)
    assert snap.positions == (("BTCUSDT", 1.0),), (
        f"seed={snap.positions[0][1]!r} — availableToWithdraw='' must not shadow walletBalance")


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
