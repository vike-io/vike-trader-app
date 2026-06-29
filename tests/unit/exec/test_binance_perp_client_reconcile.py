from __future__ import annotations
from vike_trader_app.exec.binance.perp_client import BinancePerpExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_F = {"tick_size": 0.10, "step_size": 0.001, "min_qty": 0.001, "max_qty": 120.0, "min_notional": 100.0}

# Canned fapi balance response: USDT balance = 4200.0
_BAL_RESP = [
    {"asset": "USDT", "balance": "4200.0", "availableBalance": "4100.0",
     "crossWalletBalance": "4200.0"},
    {"asset": "BNB", "balance": "0.5", "availableBalance": "0.5",
     "crossWalletBalance": "0.5"},
]

_LONG_POS_RESP = [{"symbol": "BTCUSDT", "positionAmt": "0.050", "entryPrice": "65000",
                   "markPrice": "65100", "positionSide": "BOTH", "leverage": "3"}]

_FLAT_POS_RESP = [{"symbol": "BTCUSDT", "positionAmt": "0.000", "entryPrice": "0",
                   "markPrice": "65100", "positionSide": "BOTH"}]


def _client(transport):
    return BinancePerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                      leverage=3.0, transport=transport,
                                      public_transport=lambda *a, **k: {})


def _dual_transport(pos_resp, bal_resp):
    """Route by path: /fapi/v2/positionRisk OR /fapi/v2/balance."""
    def t(base, path, method, params, signer, **k):
        if path == "/fapi/v2/positionRisk":
            return pos_resp
        if path == "/fapi/v2/balance":
            return bal_resp
        raise AssertionError(f"unexpected path: {path}")
    return t


def test_reconcile_long_signed_base_and_mark():
    snap = _client(_dual_transport(_LONG_POS_RESP, _BAL_RESP)).connect()
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTCUSDT", 0.05),)            # positionAmt already signed in base
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert snap.position_mark_px == (("BTCUSDT", 65100.0),)


def test_reconcile_balance_set_from_usdt_wallet():
    """reconcile_positions populates balance from USDT balance field on /fapi/v2/balance."""
    snap = _client(_dual_transport(_LONG_POS_RESP, _BAL_RESP)).connect()
    assert snap.balance == 4200.0


def test_reconcile_flat_balance_set():
    """Flat position path also sets balance."""
    snap = _client(_dual_transport(_FLAT_POS_RESP, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.balance == 4200.0


def test_reconcile_balance_failure_defaults_zero():
    """If the balance fetch raises, balance falls back to 0.0 and positions still returned."""
    def t(base, path, method, params, signer, **k):
        if path == "/fapi/v2/positionRisk":
            return _LONG_POS_RESP
        raise RuntimeError("network error")
    snap = _client(t).connect()
    assert snap.balance == 0.0
    assert snap.positions == (("BTCUSDT", 0.05),)


def test_reconcile_balance_missing_usdt_defaults_zero():
    """If USDT absent from fapi balance list, defaults to 0.0."""
    no_usdt = [{"asset": "BNB", "balance": "0.5", "availableBalance": "0.5"}]
    snap = _client(_dual_transport(_LONG_POS_RESP, no_usdt)).connect()
    assert snap.balance == 0.0
    assert snap.positions == (("BTCUSDT", 0.05),)


def test_reconcile_short_is_negative_base():
    short_resp = [{"symbol": "BTCUSDT", "positionAmt": "-0.200", "entryPrice": "65000",
                   "markPrice": "64900", "positionSide": "BOTH"}]
    assert _client(_dual_transport(short_resp, _BAL_RESP)).connect().positions == (("BTCUSDT", -0.2),)


def test_reconcile_flat_when_amt_zero_string():
    snap = _client(_dual_transport(_FLAT_POS_RESP, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)


def test_reconcile_emits_both_hedge_legs():
    """Hedge mode: a LONG row and a SHORT row each become a signed snapshot leg with its side."""
    hedge_resp = [{"symbol": "BTCUSDT", "positionAmt": "0.05", "positionSide": "LONG",
                   "entryPrice": "65000", "markPrice": "65100"},
                  {"symbol": "BTCUSDT", "positionAmt": "-0.03", "positionSide": "SHORT",
                   "entryPrice": "64000", "markPrice": "65100"}]
    snap = _client(_dual_transport(hedge_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.05), ("BTCUSDT", -0.03))
    assert snap.position_avg_px == (("BTCUSDT", 65000.0), ("BTCUSDT", 64000.0))
    assert snap.position_sides == (("BTCUSDT", "LONG"), ("BTCUSDT", "SHORT"))


def test_reconcile_net_only_has_no_position_sides():
    """One-way: a single BOTH row -> one leg, position_sides stays () (byte-equivalent)."""
    net_resp = [{"symbol": "BTCUSDT", "positionAmt": "0.07", "positionSide": "BOTH",
                 "entryPrice": "65000", "markPrice": "65100"}]
    snap = _client(_dual_transport(net_resp, _BAL_RESP)).connect()
    assert snap.positions == (("BTCUSDT", 0.07),)
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert snap.position_sides == ()


def test_reconcile_missing_markprice_zero():
    no_mark_resp = [{"symbol": "BTCUSDT", "positionAmt": "0.05", "entryPrice": "65000",
                     "positionSide": "BOTH"}]   # no markPrice
    assert _client(_dual_transport(no_mark_resp, _BAL_RESP)).connect().position_mark_px == (("BTCUSDT", 0.0),)
