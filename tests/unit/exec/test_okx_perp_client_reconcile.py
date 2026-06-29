"""Tests for OKXPerpExecutionClient reconcile_positions (offline, fake transport)."""
from __future__ import annotations

from vike_trader_app.exec.okx.perp_client import OKXPerpExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_F = {"tick_size": 0.1, "step_size": 0.1, "min_qty": 0.1, "max_qty": 1e4, "min_notional": 0.0}

# Canned balance response: USDT cashBal (TOTAL) = 3500.0, availBal (free) = 3200.0 — distinct so
# the test verifies reconcile reads cashBal (total, incl. locked), NOT availBal (free).
_BAL_RESP = {"code": "0", "data": [
    {"details": [
        {"ccy": "USDT", "availBal": "3200.0", "cashBal": "3500.0"},
        {"ccy": "BTC", "availBal": "0.05", "cashBal": "0.05"},
    ]}]}

_FLAT_RESP = {"code": "0", "data": []}


def _client(transport):
    return OKXPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                  symbol="BTC-USDT-SWAP", filters=_F, base_asset="BTC",
                                  ct_val=0.01, leverage=3.0,
                                  transport=transport, public_transport=lambda *a, **k: {})


def _dual_transport(pos_resp, bal_resp):
    """Route by path: positions OR account balance."""
    def t(base, path, method, params, signer, **k):
        if path == "/api/v5/account/positions":
            return pos_resp
        if path == "/api/v5/account/balance":
            return bal_resp
        raise AssertionError(f"unexpected path: {path}")
    return t


_LONG_POS_RESP = {"code": "0", "data": [
    {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "5",
     "avgPx": "65000", "markPx": "65100", "lever": "3"}]}


def test_reconcile_long_signed_base_and_mark():
    """5 contracts × ct_val=0.01 = 0.05 BTC. pos already signed (long>0)."""
    snap = _client(_dual_transport(_LONG_POS_RESP, _BAL_RESP)).connect()
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTC-USDT-SWAP", 0.05),)        # 5 contracts × 0.01 = 0.05 BTC
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 65000.0),)
    assert snap.position_mark_px == (("BTC-USDT-SWAP", 65100.0),)


def test_reconcile_balance_set_from_usdt_avail():
    """reconcile_positions populates balance from USDT cashBal (TOTAL, incl. locked)."""
    snap = _client(_dual_transport(_LONG_POS_RESP, _BAL_RESP)).connect()
    assert snap.balance == 3500.0


def test_reconcile_flat_balance_set():
    """Flat position path also sets balance."""
    snap = _client(_dual_transport(_FLAT_RESP, _BAL_RESP)).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.0),)
    assert snap.balance == 3500.0


def test_reconcile_balance_failure_defaults_zero():
    """If the balance fetch raises, balance falls back to 0.0 and positions still returned."""
    def t(base, path, method, params, signer, **k):
        if path == "/api/v5/account/positions":
            return _LONG_POS_RESP
        raise RuntimeError("network error")
    snap = _client(t).connect()
    assert snap.balance == 0.0
    assert snap.positions == (("BTC-USDT-SWAP", 0.05),)


def test_reconcile_balance_missing_usdt_defaults_zero():
    """If USDT absent from balance, defaults to 0.0."""
    no_usdt = {"code": "0", "data": [{"details": [
        {"ccy": "ETH", "availBal": "1.0", "cashBal": "1.0"}]}]}
    snap = _client(_dual_transport(_LONG_POS_RESP, no_usdt)).connect()
    assert snap.balance == 0.0
    assert snap.positions == (("BTC-USDT-SWAP", 0.05),)


def test_reconcile_short_is_negative_base():
    """pos=-20 → -20 × 0.01 = -0.2 BTC (sign on pos directly, no side lookup)."""
    short_resp = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "-20",
         "avgPx": "65000", "markPx": "64900", "lever": "3"}]}
    snap = _client(_dual_transport(short_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTC-USDT-SWAP", -0.2),)


def test_reconcile_flat_when_no_rows():
    snap = _client(_dual_transport(_FLAT_RESP, _BAL_RESP)).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.0),)
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 0.0),)
    assert snap.position_mark_px == (("BTC-USDT-SWAP", 0.0),)


def test_reconcile_flat_when_pos_zero():
    """A net row with pos='0' is treated as flat."""
    zero_resp = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "0",
         "avgPx": "65000", "markPx": "65100", "lever": "3"}]}
    snap = _client(_dual_transport(zero_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.0),)


def test_reconcile_emits_both_hedge_legs():
    """Hedge: posSide long (pos>0) + short (pos<0) -> two signed-base legs with their sides."""
    hedge_resp = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "posSide": "long", "pos": "5",
         "avgPx": "65000", "markPx": "65100", "lever": "3"},
        {"instId": "BTC-USDT-SWAP", "posSide": "short", "pos": "-3",
         "avgPx": "64000", "markPx": "65100", "lever": "3"},
    ]}
    snap = _client(_dual_transport(hedge_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.05), ("BTC-USDT-SWAP", -0.03))
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 65000.0), ("BTC-USDT-SWAP", 64000.0))
    assert snap.position_sides == (("BTC-USDT-SWAP", "LONG"), ("BTC-USDT-SWAP", "SHORT"))


def test_reconcile_net_only_has_no_position_sides():
    """One-way: a single posSide=='net' row -> one BOTH leg, position_sides () (byte-equivalent)."""
    net_resp = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "7",
         "avgPx": "65000", "markPx": "65100", "lever": "3"}]}
    snap = _client(_dual_transport(net_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTC-USDT-SWAP", 0.07),)
    assert snap.position_avg_px == (("BTC-USDT-SWAP", 65000.0),)
    assert snap.position_sides == ()


def test_reconcile_missing_markpx_zero():
    """net row without markPx → position_mark_px == ((sym, 0.0),)."""
    no_mark_resp = {"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "posSide": "net", "pos": "5",
         "avgPx": "65000", "lever": "3"}]}  # no markPx key
    snap = _client(_dual_transport(no_mark_resp, _BAL_RESP)).connect()
    assert snap.position_mark_px == (("BTC-USDT-SWAP", 0.0),)
