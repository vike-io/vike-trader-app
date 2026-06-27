# tests/unit/core/test_fill_model_quote_side.py
from vike_trader_app.core.model import Bar
from vike_trader_app.core.orders import Order
from vike_trader_app.core.fill_model import TickFillModel

M = TickFillModel()


def _q(bid, ask):  # a single quote tick as a degenerate bar (high == low == mid)
    mid = (bid + ask) / 2
    return Bar(ts=0, open=mid, high=mid, low=mid, close=mid, bid=bid, ask=ask)


def test_market_crosses_spread():
    b = _q(9.99, 10.01)
    assert M.fill_price(Order("market", +1, 1.0), b) == 10.01   # buy @ ask
    assert M.fill_price(Order("market", -1, 1.0), b) == 9.99    # sell @ bid


def test_buy_limit_triggers_on_ask_fills_at_ask():
    assert M.fill_price(Order("limit", +1, 1.0, price=10.00), _q(9.98, 10.00)) == 10.00  # ask<=limit
    assert M.fill_price(Order("limit", +1, 1.0, price=10.00), _q(10.01, 10.03)) is None  # ask>limit


def test_sell_stop_triggers_on_bid():
    assert M.fill_price(Order("stop", -1, 1.0, price=10.00), _q(9.99, 10.01)) == 9.99   # bid<=stop -> fill@bid
    assert M.fill_price(Order("stop", -1, 1.0, price=10.00), _q(10.05, 10.07)) is None  # bid>stop


def test_consolidated_bar_delegates_unchanged():
    # high != low -> NOT a single tick -> Slice-1 path (order_fill_price): market fills at open.
    cbar = Bar(ts=0, open=10.0, high=11.0, low=9.0, close=10.5, bid=9.99, ask=10.01)
    assert M.fill_price(Order("market", +1, 1.0), cbar) == 10.0   # bar.open, not ask
