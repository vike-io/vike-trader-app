"""A strategy must be able to read its current position and equity from the engine."""

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


class _RecordsPosition(Strategy):
    def __init__(self):
        super().__init__()
        self.seen_sizes = []

    def on_bar(self, bar):
        self.seen_sizes.append(self.position.size)
        if self.index == 0:
            self.buy(1.0)


def test_strategy_can_read_position_size():
    bars = [_bar(0, 100, 100), _bar(60_000, 110, 110), _bar(120_000, 120, 120)]
    strat = _RecordsPosition()
    BacktestEngine(bars, strat).run()
    # flat on bar 0, then long 1.0 after the next-open fill on bars 1 and 2
    assert strat.seen_sizes == [0.0, 1.0, 1.0]


def test_strategy_can_read_equity():
    bars = [_bar(0, 100, 100), _bar(60_000, 110, 110)]

    class _ReadsEquity(Strategy):
        def __init__(self):
            super().__init__()
            self.eq = None

        def on_bar(self, bar):
            self.eq = self.equity

    strat = _ReadsEquity()
    BacktestEngine(bars, strat, cash=5_000.0).run()
    assert strat.eq == 5_000.0
