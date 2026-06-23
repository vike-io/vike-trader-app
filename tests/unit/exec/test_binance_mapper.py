"""Pure executionReport mapper: each x/X combo -> the right vike event(s); dual-publish on TRADE."""

from vike_trader_app.exec.binance.mapper import map_execution_report
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


def _frame(**kw):
    base = {"e": "executionReport", "s": "BTCUSDT", "c": "sess-1", "S": "BUY",
            "x": "NEW", "X": "NEW", "i": 42, "l": "0", "L": "0", "n": "0",
            "m": False, "t": -1, "T": 1700, "r": "NONE"}
    base.update(kw)
    return base


def test_new_maps_to_order_accepted():
    out = map_execution_report(_frame(x="NEW", X="NEW"), venue="binance", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderAccepted)
    assert out[0].client_order_id == "sess-1"
    assert out[0].venue_order_id == "42"


def test_trade_partial_emits_fill_and_partially_filled():
    f = _frame(x="TRADE", X="PARTIALLY_FILLED", l="0.5", L="65000", n="0.01", m=True, t=777)
    out = map_execution_report(f, venue="binance", symbol="BTCUSDT")
    fills = [e for e in out if isinstance(e, FillEvent)]
    wraps = [e for e in out if isinstance(e, OrderPartiallyFilled)]
    assert len(fills) == 1 and len(wraps) == 1
    fe = fills[0]
    assert (fe.trade_id, fe.client_order_id, fe.side) == ("777", "sess-1", +1)
    assert (fe.last_qty, fe.last_px, fe.commission, fe.liquidity_side) == (0.5, 65000.0, 0.01, "maker")
    assert wraps[0].fill is fe  # the wrap carries the SAME FillEvent


def test_trade_filled_emits_fill_and_filled():
    f = _frame(x="TRADE", X="FILLED", l="1.0", L="65000", S="SELL", m=False, t=888)
    out = map_execution_report(f, venue="binance", symbol="BTCUSDT")
    assert any(isinstance(e, FillEvent) and e.side == -1 and e.liquidity_side == "taker" for e in out)
    assert any(isinstance(e, OrderFilled) for e in out)


def test_canceled_rejected_expired():
    assert isinstance(map_execution_report(_frame(x="CANCELED", X="CANCELED"),
                      venue="binance", symbol="BTCUSDT")[0], OrderCanceled)
    rej = map_execution_report(_frame(x="REJECTED", X="REJECTED", r="INSUFFICIENT_BALANCE"),
                               venue="binance", symbol="BTCUSDT")[0]
    assert isinstance(rej, OrderRejected) and rej.reason == "INSUFFICIENT_BALANCE"
    assert isinstance(map_execution_report(_frame(x="EXPIRED", X="EXPIRED"),
                      venue="binance", symbol="BTCUSDT")[0], OrderExpired)


def test_unknown_exec_type_is_ignored():
    assert map_execution_report(_frame(x="TRADE_PREVENTION"), venue="binance", symbol="BTCUSDT") == []
