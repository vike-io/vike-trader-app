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


def test_reconcile_skips_hedge_mode_rows():
    """positionIdx != 0 rows (HEDGE mode: 1=Long, 2=Short) must be ignored entirely."""
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            # positionIdx=1 and 2 are HEDGE-mode rows — must be skipped
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "65000",
             "markPrice": "65100", "positionIdx": 1},
            {"symbol": "BTCUSDT", "side": "Sell", "size": "0.1", "avgPrice": "65000",
             "markPrice": "65100", "positionIdx": 2},
        ]}}
    snap = _client(t).connect()
    # No positionIdx==0 row => treated as flat
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.position_avg_px == (("BTCUSDT", 0.0),)
    assert snap.position_mark_px == (("BTCUSDT", 0.0),)


def test_reconcile_accepts_one_way_row_among_hedge_noise():
    """If positionIdx==0 row is present alongside hedge rows, accept only that one."""
    def t(base, path, method, params, signer, **k):
        return {"retCode": 0, "result": {"list": [
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.07", "avgPrice": "66000",
             "markPrice": "66200", "positionIdx": 0},
            # stray hedge rows that must be ignored
            {"symbol": "BTCUSDT", "side": "Buy", "size": "0.5", "avgPrice": "60000",
             "markPrice": "66200", "positionIdx": 1},
        ]}}
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.07),)
    assert snap.position_avg_px == (("BTCUSDT", 66000.0),)
