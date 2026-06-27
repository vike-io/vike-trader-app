"""Tests for OKXPerpExecutionClient reconcile_positions (offline, fake transport)."""
from __future__ import annotations

from vike_trader_app.exec.okx.perp_client import OKXPerpExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_F = {"tick_size": 0.1, "step_size": 0.1, "min_qty": 0.1, "max_qty": 1e4, "min_notional": 0.0}


def _client(transport):
    return OKXPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                  symbol="BTC-USDT-SWAP", filters=_F, base_asset="BTC",
                                  ct_val=0.01, leverage=3.0,
                                  transport=transport, public_transport=lambda *a, **k: {})


def test_reconcile_long_signed_base_and_mark():
    """5 contracts × ct_val=0.01 = 0.05 BTC. pos already signed (long>0)."""

    def t(base, path, method, params, signer, **k):
        assert path == "/api/v5/account/positions"
        assert params == {"instType": "SWAP", "instId": "BTC-USDT-SWAP"}
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "5",
             "avgPx": "65000", "markPx": "65100", "lever": "3"}]}

    snap = _client(t).connect()             # PRODUCT=='perp' routes to reconcile_positions
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTC-USDT-SWAP", 0.05),)        # 5 contracts × 0.01 = 0.05 BTC
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 65000.0),)
    assert snap.position_mark_px == (("BTC-USDT-SWAP", 65100.0),)


def test_reconcile_short_is_negative_base():
    """pos=-20 → -20 × 0.01 = -0.2 BTC (sign on pos directly, no side lookup)."""

    def t(base, path, method, params, signer, **k):
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "-20",
             "avgPx": "65000", "markPx": "64900", "lever": "3"}]}

    snap = _client(t).connect()
    assert snap.positions == (("BTC-USDT-SWAP", -0.2),)


def test_reconcile_flat_when_no_rows():
    def t(base, path, method, params, signer, **k):
        return {"code": "0", "data": []}

    snap = _client(t).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.0),)
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 0.0),)
    assert snap.position_mark_px == (("BTC-USDT-SWAP", 0.0),)


def test_reconcile_flat_when_pos_zero():
    """A net row with pos='0' is treated as flat."""

    def t(base, path, method, params, signer, **k):
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "0",
             "avgPx": "65000", "markPx": "65100", "lever": "3"}]}

    snap = _client(t).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.0),)


def test_reconcile_emits_both_hedge_legs():
    """Hedge: posSide long (pos>0) + short (pos<0) -> two signed-base legs with their sides."""
    def t(base, path, method, params, signer, **k):
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "5",
             "avgPx": "65000", "markPx": "65100", "lever": "3"},
            {"instId": "BTC-USDT-SWAP", "posSide": "short", "pos": "-3",
             "avgPx": "64000", "markPx": "65100", "lever": "3"},
        ]}
    snap = _client(t).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.05), ("BTC-USDT-SWAP", -0.03))
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 65000.0), ("BTC-USDT-SWAP", 64000.0))
    assert snap.position_sides == (("BTC-USDT-SWAP", "LONG"), ("BTC-USDT-SWAP", "SHORT"))


def test_reconcile_net_only_has_no_position_sides():
    """One-way: a single posSide=='net' row -> one BOTH leg, position_sides () (byte-equivalent)."""
    def t(base, path, method, params, signer, **k):
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "7",
             "avgPx": "65000", "markPx": "65100", "lever": "3"}]}
    snap = _client(t).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.07),)
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 65000.0),)
    assert snap.position_sides == ()


def test_reconcile_missing_markpx_zero():
    """net row without markPx → position_mark_px == ((sym, 0.0),)."""

    def t(base, path, method, params, signer, **k):
        return {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "5",
             "avgPx": "65000", "lever": "3"}]}  # no markPx key

    snap = _client(t).connect()
    assert snap.position_mark_px == (("BTC-USDT-SWAP", 0.0),)
