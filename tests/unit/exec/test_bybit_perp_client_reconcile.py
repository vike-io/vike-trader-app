"""Tests for BybitPerpExecutionClient reconcile_positions (offline, fake transport)."""
from __future__ import annotations

import pytest

from vike_trader_app.exec.bybit.perp_client import BybitPerpExecutionClient
from vike_trader_app.exec.bybit.transport import BybitApiError
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_F = {"tick_size": 0.1, "step_size": 0.001, "min_qty": 0.001, "max_qty": 9e3, "min_notional": 5.0}

# Canned position-list response (one Buy leg).
_POS_RESP = {"retCode": 0, "result": {"list": [
    {"symbol": "BTCUSDT", "side": "Buy", "size": "0.05", "avgPrice": "65000",
     "markPrice": "65100", "leverage": "3", "liqPrice": "40000", "positionIdx": 0}]}}

# Canned balance response (USDT 5000 walletBalance).
_BAL_RESP = {"retCode": 0, "result": {"list": [
    {"accountType": "UNIFIED", "coin": [
        {"coin": "USDT", "walletBalance": "5000.0", "availableToWithdraw": ""},
        {"coin": "BTC", "walletBalance": "0.1", "availableToWithdraw": ""},
    ]}]}}

_FLAT_RESP = {"retCode": 0, "result": {"list": []}}


def _client(transport):
    return BybitPerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                    symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                    transport=transport, public_transport=lambda *a, **k: {})


def _dual_transport(pos_resp, bal_resp):
    """Return a transport that routes by path: position list OR account balance."""
    def t(base, path, method, params, signer, **k):
        if path == "/v5/position/list":
            return pos_resp
        if path == "/v5/account/wallet-balance":
            return bal_resp
        raise AssertionError(f"unexpected path: {path}")
    return t


def test_reconcile_signs_long_and_seeds_mark():
    snap = _client(_dual_transport(_POS_RESP, _BAL_RESP)).connect()
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTCUSDT", 0.05),)         # Buy -> +
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert snap.position_mark_px == (("BTCUSDT", 65100.0),)


def test_reconcile_balance_set_from_usdt_wallet():
    """reconcile_positions populates balance from USDT walletBalance."""
    snap = _client(_dual_transport(_POS_RESP, _BAL_RESP)).connect()
    assert snap.balance == 5000.0


def test_reconcile_flat_balance_set():
    """Flat position path also sets balance."""
    snap = _client(_dual_transport(_FLAT_RESP, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.balance == 5000.0


def test_reconcile_balance_failure_defaults_zero():
    """If the balance fetch raises, balance falls back to 0.0 and positions still returned."""
    def t(base, path, method, params, signer, **k):
        if path == "/v5/position/list":
            return _POS_RESP
        raise RuntimeError("network error")
    snap = _client(t).connect()
    assert snap.balance == 0.0
    assert snap.positions == (("BTCUSDT", 0.05),)   # positions unaffected


def test_reconcile_balance_missing_usdt_defaults_zero():
    """If USDT is absent from the balance list, balance defaults to 0.0."""
    no_usdt_bal = {"retCode": 0, "result": {"list": [
        {"accountType": "UNIFIED", "coin": [
            {"coin": "ETH", "walletBalance": "2.0", "availableToWithdraw": ""}]}]}}
    snap = _client(_dual_transport(_POS_RESP, no_usdt_bal)).connect()
    assert snap.balance == 0.0
    assert snap.positions == (("BTCUSDT", 0.05),)


def test_reconcile_signs_short_negative():
    short_resp = {"retCode": 0, "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.2", "avgPrice": "65000",
         "markPrice": "64900", "positionIdx": 0}]}}
    snap = _client(_dual_transport(short_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", -0.2),)         # Sell -> negative


def test_reconcile_flat_when_no_positions():
    snap = _client(_dual_transport(_FLAT_RESP, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.position_avg_px == (("BTCUSDT", 0.0),)
    assert snap.position_mark_px == (("BTCUSDT", 0.0),)


def test_reconcile_emits_both_hedge_legs():
    """Hedge: positionIdx 1 (Long, Buy) + 2 (Short, Sell) -> two signed legs with their sides."""
    hedge_resp = {"retCode": 0, "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.1", "avgPrice": "65000",
         "markPrice": "65100", "positionIdx": 1},
        {"symbol": "BTCUSDT", "side": "Sell", "size": "0.04", "avgPrice": "64000",
         "markPrice": "65100", "positionIdx": 2},
    ]}}
    snap = _client(_dual_transport(hedge_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.1), ("BTCUSDT", -0.04))
    assert snap.position_avg_px == (("BTCUSDT", 65000.0), ("BTCUSDT", 64000.0))
    assert snap.position_sides == (("BTCUSDT", "LONG"), ("BTCUSDT", "SHORT"))


def test_reconcile_net_only_idx0_has_no_position_sides():
    """One-way: a single positionIdx==0 row -> one BOTH leg, position_sides () (byte-equivalent)."""
    net_resp = {"retCode": 0, "result": {"list": [
        {"symbol": "BTCUSDT", "side": "Buy", "size": "0.07", "avgPrice": "66000",
         "markPrice": "66200", "positionIdx": 0}]}}
    snap = _client(_dual_transport(net_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.07),)
    assert snap.position_avg_px == (("BTCUSDT", 66000.0),)
    assert snap.position_sides == ()
