from vike_trader_app.core.model import Bar
from vike_trader_app.core.orders import Order
from vike_trader_app.core.fill_model import BarFillModel, TickFillModel


def test_bar_model_market_fills_at_open():
    bar = Bar(ts=0, open=10.0, high=11, low=9, close=10.5)
    assert BarFillModel().fill_price(Order("market", +1, 1.0), bar) == 10.0


def test_quote_model_crosses_spread_for_market():
    bar = Bar(ts=0, open=10.0, high=11, low=9, close=10.5, bid=9.99, ask=10.01)
    q = TickFillModel()
    assert q.fill_price(Order("market", +1, 1.0), bar) == 10.01   # buy @ ask (any bar shape)
    assert q.fill_price(Order("market", -1, 1.0), bar) == 9.99    # sell @ bid


def test_quote_model_falls_back_without_quote():
    bar = Bar(ts=0, open=10.0, high=11, low=9, close=10.5)  # no bid/ask
    assert TickFillModel().fill_price(Order("market", +1, 1.0), bar) == 10.0
