"""OKX connect() reconciles: account/balance details[].availBal (FREE) -> free;
open orders -> ACCEPTED ManagedOrders; ticker.last -> avg_px.

BALANCE_IS_TOTAL=False: availBal is the FREE portion.  The base adds locked_sell_qty
(open SELL remaining qty) back on top to reconstruct the true total held.

Ground truth: availBal="1.0", cashBal="1.15" (cashBal/eq are totals — must NOT be read).
seeded_size = availBal + locked_sell = 1.0 + 0.15 = 1.15.
"""

from __future__ import annotations

from vike_trader_app.exec.okx.client import OKXSpotExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.order import ManagedOrder, OrderStatus

_FILTERS = {"tick_size": 0.01, "step_size": 0.001, "min_qty": 0.001,
            "max_qty": 9000.0, "min_notional": 5.0}


def _client(transport, public_transport):
    return OKXSpotExecutionClient(
        EventBus(), signer=object(), rest_base_url="https://x", symbol="BTC-USDT",
        filters=_FILTERS, base_asset="BTC", transport=transport, public_transport=public_transport)


def test_connect_seeds_availbal_plus_locked_sell():
    """availBal is FREE; locked-sell must be ADDED back (BALANCE_IS_TOTAL=False).
    seed = availBal 1.0 + open-SELL remaining 0.15 = 1.15."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v5/account/balance":
            assert params == {}
            return {"code": "0", "data": [{"details": [
                {"ccy": "BTC", "availBal": "1.0", "cashBal": "1.15", "frozenBal": "0.15"},
                {"ccy": "USDT", "availBal": "5000", "cashBal": "5000"},
            ]}]}
        if path == "/api/v5/trade/orders-pending":
            assert params == {"instType": "SPOT", "instId": "BTC-USDT"}
            return {"code": "0", "data": [
                {"clOrdId": "prev-9", "ordId": "7", "side": "sell", "ordType": "limit",
                 "px": "70000", "sz": "0.15", "accFillSz": "0.0"}
            ]}
        raise AssertionError(path)

    def _public(base, path, params):
        assert path == "/api/v5/market/ticker"
        return {"code": "0", "data": [{"instId": "BTC-USDT", "last": "68000.0"}]}

    snap = _client(_transport, _public).connect()
    assert isinstance(snap, ReconcileSnapshot)
    # seed = availBal(1.0) + locked-sell(0.15); BALANCE_IS_TOTAL=False re-adds the open SELL qty
    assert snap.positions == (("BTC-USDT", 1.15),)
    assert snap.position_avg_px == (("BTC-USDT", 68000.0),)
    assert len(snap.open_orders) == 1
    mo = snap.open_orders[0]
    assert isinstance(mo, ManagedOrder)
    assert mo.status is OrderStatus.ACCEPTED
    assert mo.client_order_id == "prev-9"
    assert mo.venue_order_id == "7"


def test_uses_availbal_not_cashbal():
    """Regression: availBal (free) must be read, NOT cashBal or eq (totals).
    No open orders -> seed = availBal only = 1.0."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v5/account/balance":
            return {"code": "0", "data": [{"details": [
                {"ccy": "BTC", "availBal": "1.0", "cashBal": "1.15", "eq": "1.2"},
            ]}]}
        if path == "/api/v5/trade/orders-pending":
            return {"code": "0", "data": []}
        raise AssertionError(path)

    def _public(base, path, params):
        return {"code": "0", "data": [{"instId": "BTC-USDT", "last": "68000.0"}]}

    snap = _client(_transport, _public).connect()
    # Must be 1.0 (availBal), NOT 1.15 (cashBal) or 1.2 (eq)
    assert snap.positions == (("BTC-USDT", 1.0),), (
        f"seed={snap.positions[0][1]!r} — must read availBal not cashBal/eq")


def test_connect_no_base_balance_seeds_zero():
    """When the base asset (BTC) is absent from the balance details, seed = 0."""
    def _transport(base, path, method, params, signer, **kw):
        if path == "/api/v5/account/balance":
            return {"code": "0", "data": [{"details": [
                {"ccy": "USDT", "availBal": "5000", "cashBal": "5000"},
            ]}]}
        if path == "/api/v5/trade/orders-pending":
            return {"code": "0", "data": []}
        raise AssertionError(path)

    def _public(base, path, params):
        return {"code": "0", "data": [{"instId": "BTC-USDT", "last": "68000.0"}]}

    snap = _client(_transport, _public).connect()
    assert snap.positions == (("BTC-USDT", 0.0),)
    assert snap.open_orders == ()
