import pytest
from vike_trader_app.core.ticks import QuoteTick
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.tick_backtest import run_tick_backtest, NoTickData
from vike_trader_app.data import tick_store


class BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.close()


def test_strict_mode_raises_without_tick_data(tmp_path):
    with pytest.raises(NoTickData):
        run_tick_backtest(BuyThenClose(), symbol="EURUSD", interval="1m",
                          start_ms=0, end_ms=600_000, root=str(tmp_path), kind="quotes")


def test_end_to_end_quote_backtest_crosses_spread(tmp_path):
    root = str(tmp_path)
    # three 1-minute buckets, each with one opening quote
    quotes = [
        QuoteTick(ts=0, bid=9.99, ask=10.01),
        QuoteTick(ts=60_000, bid=19.95, ask=20.05),
        QuoteTick(ts=120_000, bid=29.90, ask=30.10),
    ]
    tick_store.write_quotes(quotes, root, "EURUSD")
    result = run_tick_backtest(BuyThenClose(), symbol="EURUSD", interval="1m",
                               start_ms=0, end_ms=180_000, root=root, kind="quotes")
    # entry buy filled at bucket-1 ask (20.05); exit close (a sell) at bucket-2 bid (29.90)
    assert result.trades[0].entry_price == 20.05
    assert result.trades[0].exit_price == 29.90
