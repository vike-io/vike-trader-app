"""Tests for BybitPerpExecutionClient reconcile_positions (offline, fake transport)."""
from __future__ import annotations

from vike_trader_app.exec.bybit.perp_client import BybitPerpExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_F = {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001, "max_qty": 9e3, "min_notional": 5.0}


def _client(transport):
    return BybitPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                    symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                    transport=transport, public_transport=lambda *a, **k: {})


def test_reconcile_signs_long_and_seeds_mark():
    def t(base, path, method, params, signer, **k):
        assert path == "/v5/position/list"
        assert params == {"category": "linear", "symbol": "BTCUSDT"}
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.05", "avgPrice": "65000",
             "markPrice": "65100", "leverage": "3", "liqPrice": "40000", "positionIdx": 0}]}}
    snap = _client(t).connect()               # PRODUCT=='perp' routes to reconcile_positions
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTCUSDT", 0.05),)         # Buy -> +
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert snap.position_mark_px == (("BTCUSDT", 65100.0),)


def test_reconcile_signs_short_negative():
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Sell", "size": "0.2", "avgPrice": "65000",
             "markPrice": "64900", "positionIdx": 0}]}}
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", -0.2),)         # Sell -> negative


def test_reconcile_flat_when_no_positions():
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": []}}
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.position_avg_px == (("BTCUSDT", 0.0),)
    assert snap.position_mark_px == (("BTCUSDT", 0.0),)


def test_reconcile_emits_both_hedge_legs():
    """Hedge: positionIdx 1 (Long, Buy) + 2 (Short, Sell) -> two signed legs with their sides."""
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "65000",
             "markPrice": "65100", "positionIdx": 1},
            {"symbol": "BTCUSDT", "side": "Sell", "size": "0.04", "avgPrice": "64000",
             "markPrice": "65100", "positionIdx": 2},
        ]}}
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.1), ("BTCUSDT", -0.04))
    assert snap.position_avg_px == (("BTCUSDT", 65000.0), ("BTCUSDT", 64000.0))
    assert snap.position_sides == (("BTCUSDT", "LONG"), ("BTCUSDT", "SHORT"))


def test_reconcile_net_only_idx0_has_no_position_sides():
    """One-way: a single positionIdx==0 row -> one BOTH leg, position_sides () (byte-equivalent)."""
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.07", "avgPrice": "66000",
             "markPrice": "66200", "positionIdx": 0}]}}
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.07),)
    assert snap.position_avg_px == (("BTCUSDT", 66000.0),)
    assert snap.position_sides == ()
