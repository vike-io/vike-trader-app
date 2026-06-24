from __future__ import annotations
from vike_trader_app.exec.binance.perp_client import BinancePerpExecutionClient
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_F = {"tick_size": 0.10, "step_size": 0.001, "min_qty": 0.001, "max_qty": 120.0, "min_notional": 100.0}

def _client(transport):
    return BinancePerpExecutionClient(EventBus(), signer=object(), rest_base_url="https://x",
                                      symbol="BTCUSDT", filters=_F, base_asset="BTC",
                                      leverage=3.0, transport=transport,
                                      public_transport=lambda *a, **k: {})

def test_reconcile_long_signed_base_and_mark():
    def t(base, path, method, params, signer, **k):
        assert path == "/fapi/v2/positionRisk" and method == "GET"
        assert params == {"symbol": "BTCUSDT"}
        return [{"symbol": "BTCUSDT", "positionAmt": "0.050", "entryPrice": "65000",
                 "markPrice": "65100", "positionSide": "BOTH", "leverage": "3"}]
    snap = _client(t).connect()        # PRODUCT=='perp' routes to reconcile_positions
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == (("BTCUSDT", 0.05),)            # positionAmt already signed in base
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)
    assert snap.position_mark_px == (("BTCUSDT", 65100.0),)

def test_reconcile_short_is_negative_base():
    def t(base, path, method, params, signer, **k):
        return [{"symbol": "BTCUSDT", "positionAmt": "-0.200", "entryPrice": "65000",
                 "markPrice": "64900", "positionSide": "BOTH"}]
    assert _client(t).connect().positions == (("BTCUSDT", -0.2),)

def test_reconcile_flat_when_amt_zero_string():
    def t(base, path, method, params, signer, **k):
        return [{"symbol": "BTCUSDT", "positionAmt": "0.000", "entryPrice": "0",
                 "markPrice": "65100", "positionSide": "BOTH"}]
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)

def test_reconcile_skips_hedge_rows():
    def t(base, path, method, params, signer, **k):
        return [{"symbol": "BTCUSDT", "positionAmt": "0.05", "positionSide": "LONG",
                 "entryPrice": "65000", "markPrice": "65100"},
                {"symbol": "BTCUSDT", "positionAmt": "-0.05", "positionSide": "SHORT",
                 "entryPrice": "65000", "markPrice": "65100"}]
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.0),)
    assert snap.position_avg_px == (("BTCUSDT", 0.0),)
    assert snap.position_mark_px == (("BTCUSDT", 0.0),)

def test_reconcile_accepts_both_among_hedge_noise():
    def t(base, path, method, params, signer, **k):
        return [{"symbol": "BTCUSDT", "positionAmt": "0.1", "positionSide": "LONG",
                 "entryPrice": "64000", "markPrice": "65100"},
                {"symbol": "BTCUSDT", "positionAmt": "0.07", "positionSide": "BOTH",
                 "entryPrice": "65000", "markPrice": "65100"}]
    snap = _client(t).connect()
    assert snap.positions == (("BTCUSDT", 0.07),)
    assert snap.position_avg_px == (("BTCUSDT", 65000.0),)

def test_reconcile_missing_markprice_zero():
    def t(base, path, method, params, signer, **k):
        return [{"symbol": "BTCUSDT", "positionAmt": "0.05", "entryPrice": "65000",
                 "positionSide": "BOTH"}]   # no markPrice
    assert _client(t).connect().position_mark_px == (("BTCUSDT", 0.0),)
