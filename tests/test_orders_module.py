"""Pure trigger function shared by both engines (extracted from BacktestEngine)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.orders import Order, order_fill_price


def _bar(o, h, l, c):
    return Bar(ts=0, open=o, high=h, low=l, close=c, volume=1.0)


def test_market_fills_at_open():
    assert order_fill_price(Order("market", +1, 1.0), _bar(100, 101, 99, 100)) == 100


def test_limit_buy_triggers_on_dip_else_none():
    assert order_fill_price(Order("limit", +1, 1.0, price=95.0), _bar(100, 101, 94, 96)) == 95.0
    assert order_fill_price(Order("limit", +1, 1.0, price=95.0), _bar(100, 102, 98, 101)) is None


def test_stop_buy_triggers_on_breakout_else_none():
    assert order_fill_price(Order("stop", +1, 1.0, price=105.0), _bar(103, 106, 102, 105)) == 105.0
    assert order_fill_price(Order("stop", +1, 1.0, price=105.0), _bar(100, 104, 99, 102)) is None


def test_trailing_sell_ratchets_then_triggers():
    o = Order("trailing", -1, 1.0, trail=5.0, extreme=100.0)
    # new-high bar: trigger 95, low 100 > 95 -> no fill; extreme ratchets up to 110
    assert order_fill_price(o, _bar(100, 110, 100, 110)) is None
    assert o.extreme == 110.0
    # next bar: trigger 110-5=105, low 104 <= 105 -> fills at 105 (does NOT stop on its own low first)
    assert order_fill_price(o, _bar(108, 108, 104, 104)) == 105.0
