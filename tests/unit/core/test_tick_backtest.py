import pytest
from vike_trader_app.core.ticks import QuoteTick, TradeTick
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
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


def test_strict_mode_raises_without_trade_data(tmp_path):
    with pytest.raises(NoTickData):
        run_tick_backtest(BuyThenClose(), symbol="BTCUSDT", interval="1m",
                          start_ms=0, end_ms=600_000, root=str(tmp_path), kind="trades")


def test_unknown_kind_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        run_tick_backtest(BuyThenClose(), symbol="X", interval="1m",
                          start_ms=0, end_ms=60_000, root=str(tmp_path), kind="bogus")


def test_end_to_end_trade_backtest_uses_bar_prices(tmp_path):
    root = str(tmp_path)
    trades = [TradeTick(ts=0, price=10.0, size=1.0),
              TradeTick(ts=60_000, price=20.0, size=1.0),
              TradeTick(ts=120_000, price=30.0, size=1.0)]
    tick_store.write_trades(trades, root, "BTCUSDT")
    result = run_tick_backtest(BuyThenClose(), symbol="BTCUSDT", interval="1m",
                               start_ms=0, end_ms=180_000, root=root, kind="trades")
    # trade bars carry no bid/ask -> TickFillModel falls back to bar prices (next-open):
    # buy@index0 fills bar1 open=20.0; close@index1 fills bar2 open=30.0
    assert result.trades[0].entry_price == 20.0
    assert result.trades[0].exit_price == 30.0
