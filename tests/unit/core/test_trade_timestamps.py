"""Trades must carry entry/exit timestamps so the chart can place markers."""

from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars():
    return [
        _bar(0, 100, 100),
        _bar(60_000, 110, 110),
        _bar(120_000, 120, 120),
        _bar(180_000, 130, 130),
    ]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


def test_trade_records_fill_timestamps_at_next_open():
    result = SingleSymbolEngine(_bars(), _BuyThenClose(), fee_rate=0.0).run()
    t = result.trades[0]
    assert t.entry_ts == 60_000  # buy submitted at idx0 -> fills at bar idx1 (ts 60_000)
    assert t.exit_ts == 180_000  # close submitted at idx2 -> fills at bar idx3 (ts 180_000)
