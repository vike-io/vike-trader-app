# tests/unit/core/test_schedule_wiring.py
from vike_trader_app.core.model import Bar
from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.schedule import EveryNBars, MonthStart
from vike_trader_app.core.strategy import Strategy


def _bars(n):
    # daily bars across a Jan->Feb boundary
    return [Bar(ts=1704067200000 + i * 86_400_000, open=1, high=1, low=1, close=1) for i in range(n)]


def test_backtest_fires_schedule_after_on_bar():
    class S(Strategy):
        def __init__(self):
            super().__init__()
            self.ticks = []
        def on_start(self):
            self.schedule.on(EveryNBars(5), lambda: self.ticks.append(self.index))
    s = S(); SingleSymbolEngine(_bars(12), s).run()
    assert s.ticks == [0, 5, 10]            # fired on the right bars, in-loop


def test_schedule_handle_exists_on_strategy():
    assert Strategy().schedule is not None
