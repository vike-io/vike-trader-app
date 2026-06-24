from __future__ import annotations
from vike_trader_app.exec.binance.perp_mapper import map_binance_perp
from vike_trader_app.exec.events import (
    FillEvent, OrderAccepted, OrderCanceled, OrderExpired, OrderFilled, OrderPartiallyFilled,
)

def _evt(**o_over):
    o = {"s": "BTCUSDT", "c": "c-0", "S": "BUY", "o": "MARKET", "x": "TRADE", "X": "FILLED",
         "i": 99, "l": "0.012", "L": "65000", "n": "0.013", "N": "USDT", "t": 7777,
         "m": False, "R": False, "ps": "BOTH", "ap": "65000", "q": "0.012"}
    o.update(o_over)
    return {"e": "ORDER_TRADE_UPDATE", "E": 1700000000000, "T": 1700000000100, "o": o}

def test_trade_fill_dual_publish_identity_and_fields():
    evs = map_binance_perp(_evt(), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderFilled"]
    f = evs[0]
    assert f.trade_id == "7777" and f.client_order_id == "c-0"
    assert f.symbol == "BTCUSDT" and f.side == +1
    assert f.last_qty == 0.012 and f.last_px == 65000.0
    assert f.commission == 0.013 and f.liquidity_side == "taker"
    assert f.position_side == "BOTH" and f.mark_price is None
    assert f.ts == 1700000000100                    # frame['T']
    assert evs[1].fill is evs[0]                     # dual-publish identity

def test_partial_fill_wrap_from_X():
    evs = map_binance_perp(_evt(X="PARTIALLY_FILLED", l="0.004"), venue="binance", symbol="BTCUSDT")
    assert isinstance(evs[1], OrderPartiallyFilled)  # wrap off X, NOT x (every fill is x=TRADE)
    assert evs[0].last_qty == 0.004
    assert evs[1].fill is evs[0]

def test_maker_liquidity():
    evs = map_binance_perp(_evt(m=True), venue="binance", symbol="BTCUSDT")
    assert evs[0].liquidity_side == "maker"

def test_sell_side_and_posside_passthrough():
    evs = map_binance_perp(_evt(S="SELL", ps="SHORT"), venue="binance", symbol="BTCUSDT")
    assert evs[0].side == -1
    assert evs[0].position_side == "SHORT"           # Binance uses the literal strings; no map

def test_new_lifecycle_only():
    evs = map_binance_perp(_evt(x="NEW", X="NEW", t=0), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["OrderAccepted"]
    assert isinstance(evs[0], OrderAccepted) and evs[0].venue_order_id == "99"

def test_canceled_lifecycle_only():
    evs = map_binance_perp(_evt(x="CANCELED", X="CANCELED"), venue="binance", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["OrderCanceled"]
    assert isinstance(evs[0], OrderCanceled)

def test_expired_lifecycle_only():
    evs = map_binance_perp(_evt(x="EXPIRED", X="EXPIRED"), venue="binance", symbol="BTCUSDT")
    assert isinstance(evs[0], OrderExpired)

def test_calculated_and_amendment_ignored():
    assert map_binance_perp(_evt(x="CALCULATED"), venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp(_evt(x="AMENDMENT"), venue="binance", symbol="BTCUSDT") == []

def test_symbol_fallback_when_absent():
    frame = _evt(); del frame["o"]["s"]
    assert map_binance_perp(frame, venue="binance", symbol="ETHUSDT")[0].symbol == "ETHUSDT"

def test_non_order_trade_update_skipped():
    assert map_binance_perp({"e": "ACCOUNT_UPDATE", "a": {}}, venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "listenKeyExpired"}, venue="binance", symbol="BTCUSDT") == []

def test_non_dict_and_missing_o_skipped():
    assert map_binance_perp("pong", venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "ORDER_TRADE_UPDATE"}, venue="binance", symbol="BTCUSDT") == []
    assert map_binance_perp({"e": "ORDER_TRADE_UPDATE", "o": "x"}, venue="binance", symbol="BTCUSDT") == []
