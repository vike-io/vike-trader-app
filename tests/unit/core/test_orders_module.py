"""Pure trigger function shared by both engines (extracted from SingleSymbolEngine)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.orders import Order, order_fill_price, order_fill_price_granular


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


def test_market_close_fills_at_bar_close():
    bar = _bar(100, 110, 95, 108)
    assert order_fill_price(Order("market_close", +1, 1.0), bar) == 108
    assert order_fill_price(Order("market_close", -1, 1.0), bar) == 108


def test_limit_close_buy_fills_only_when_close_at_or_below_price():
    # close=108, limit=110 -> 108 <= 110 -> fills at close
    assert order_fill_price(Order("limit_close", +1, 1.0, price=110.0), _bar(100, 115, 95, 108)) == 108.0
    # close=112, limit=110 -> 112 > 110 -> no fill
    assert order_fill_price(Order("limit_close", +1, 1.0, price=110.0), _bar(100, 115, 95, 112)) is None
    # exact: close == price -> fills
    assert order_fill_price(Order("limit_close", +1, 1.0, price=108.0), _bar(100, 115, 95, 108)) == 108.0


def test_limit_close_sell_fills_only_when_close_at_or_above_price():
    # close=108, limit=105 -> 108 >= 105 -> fills at close
    assert order_fill_price(Order("limit_close", -1, 1.0, price=105.0), _bar(100, 115, 95, 108)) == 108.0
    # close=103, limit=105 -> 103 < 105 -> no fill
    assert order_fill_price(Order("limit_close", -1, 1.0, price=105.0), _bar(100, 115, 95, 103)) is None
    # exact: close == price -> fills
    assert order_fill_price(Order("limit_close", -1, 1.0, price=108.0), _bar(100, 115, 95, 108)) == 108.0


# --- granular sub-bar resolution (order_fill_price_granular) ---

def test_granular_limit_buy_triggers_at_first_dipping_sub_bar():
    o = Order("limit", +1, 1.0, price=100.0)
    subs = [_bar(105, 106, 101, 104), _bar(104, 105, 100, 102)]  # first stays > 100, second dips to 100
    fp, idx = order_fill_price_granular(o, subs)
    assert fp == 100.0
    assert idx == 1


def test_granular_stop_that_never_triggers_returns_none():
    o = Order("stop", +1, 1.0, price=200.0)
    subs = [_bar(105, 106, 101, 104), _bar(104, 105, 100, 102)]
    assert order_fill_price_granular(o, subs) is None


def test_granular_market_fills_at_first_sub_bar_open():
    o = Order("market", +1, 1.0)
    subs = [_bar(105, 106, 101, 104), _bar(104, 105, 100, 102)]
    fp, idx = order_fill_price_granular(o, subs)
    assert fp == 105.0
    assert idx == 0


def test_granular_market_close_fills_at_first_sub_bar_close():
    o = Order("market_close", +1, 1.0)
    subs = [_bar(105, 106, 101, 104), _bar(104, 105, 100, 102)]
    fp, idx = order_fill_price_granular(o, subs)
    assert fp == 104.0
    assert idx == 0


def test_granular_trailing_ratchets_across_sub_bars_then_triggers():
    o = Order("trailing", -1, 1.0, trail=5.0, extreme=100.0)
    # sub 0: new high to 110 -> trigger 95, low 100 > 95, no fill; extreme ratchets to 110
    # sub 1: trigger now 110-5=105, low 104 <= 105 -> fills at 105 (sub-index 1)
    subs = [_bar(100, 110, 100, 110), _bar(108, 108, 104, 104)]
    fp, idx = order_fill_price_granular(o, subs)
    assert fp == 105.0
    assert idx == 1
    assert o.extreme == 110.0


def test_granular_empty_sub_bars_returns_none():
    assert order_fill_price_granular(Order("market", +1, 1.0), []) is None


def test_order_weight_defaults_zero_and_is_settable():
    assert Order("market", +1, 1.0).weight == 0.0
    assert Order("market", +1, 1.0, weight=2.5).weight == 2.5


def test_strategy_limit_buy_lands_weight_in_pending():
    from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
    from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy

    class _Rest(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.limit_buy(1.0, 95.0, weight=3.0)

    eng = SingleSymbolEngine([_bar(100, 101, 99, 100), _bar(100, 101, 99, 100)], _Rest())
    eng.run()
    # the resting limit never triggered (low 99 > 95), so it's still pending with its weight
    assert eng._pending[0].weight == 3.0
    assert eng._pending[0].kind == "limit"
