"""Task-4 tests: per-tick route in run_tick_backtest.

Tests the per_tick=True branch that feeds raw ticks to engine.run_ticks().
"""
import pytest
from vike_trader_app.core.ticks import QuoteTick
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.tick_backtest import run_tick_backtest, NoTickData
from vike_trader_app.data import tick_store


class _BuyThenClose(Strategy):
    def on_quote_tick(self, tick):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


def test_per_tick_strict_raises_without_data(tmp_path):
    with pytest.raises(NoTickData):
        run_tick_backtest(_BuyThenClose(), symbol="EURUSD", interval="1m",
                          start_ms=0, end_ms=600_000, root=str(tmp_path),
                          kind="quotes", per_tick=True)


def test_per_tick_end_to_end_crosses_spread(tmp_path):
    root = str(tmp_path)
    quotes = [QuoteTick(ts=0, bid=9.99, ask=10.01),
              QuoteTick(ts=1, bid=19.95, ask=20.05),
              QuoteTick(ts=2, bid=29.90, ask=30.10),
              QuoteTick(ts=3, bid=39.90, ask=40.10)]
    tick_store.write_quotes(quotes, root, "EURUSD")
    result = run_tick_backtest(_BuyThenClose(), symbol="EURUSD", interval="1m",
                               start_ms=0, end_ms=10_000, root=root,
                               kind="quotes", per_tick=True)
    assert result.trades[0].entry_price == 20.05   # tick1 ask
    assert result.trades[0].exit_price == 39.90    # tick3 bid
