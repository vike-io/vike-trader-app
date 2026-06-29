from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.fill_model import TickFillModel
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


class BuyOnceThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.close()


def test_market_buy_fills_at_ask_with_quote_model():
    # bar0: submit buy -> fills at bar1.open. With TickFillModel it fills at bar1.ask.
    bars = [
        Bar(ts=0, open=10, high=10, low=10, close=10, bid=9.99, ask=10.01),
        Bar(ts=60, open=20, high=20, low=20, close=20, bid=19.95, ask=20.05),
        Bar(ts=120, open=30, high=30, low=30, close=30, bid=29.9, ask=30.1),
    ]
    eng = SingleSymbolEngine(bars, BuyOnceThenClose(), fill_model=TickFillModel())
    eng.run()
    # entry crossed the spread at bar1 ask = 20.05 (not bar1.open 20.0)
    assert eng.trades[0].entry_price == 20.05


def test_default_engine_unchanged_without_fill_model():
    bars = [Bar(ts=t, open=10 + t, high=10 + t, low=10 + t, close=10 + t) for t in (0, 60, 120)]
    eng = SingleSymbolEngine(bars, BuyOnceThenClose())  # BarFillModel default
    eng.run()
    assert eng.trades[0].entry_price == 70.0  # bar1.open = 10 + 60
