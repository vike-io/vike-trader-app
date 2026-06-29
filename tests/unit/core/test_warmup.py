"""Warm-up gating: a strategy must not act before bar index reaches ``WARMUP``."""

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars(n=10):
    return [_bar(i * 60_000, 100 + i, 100 + i) for i in range(n)]


class _BuyEveryBar(Strategy):
    """Tries to open a long on every bar; records the bar index where on_bar fired."""

    WARMUP = 5

    def __init__(self):
        super().__init__()
        self.fired = []

    def on_bar(self, bar):
        self.fired.append(self.index)
        if self.position.size == 0:
            self.buy(1.0)


def test_warmup_gates_on_bar_before_index():
    strat = _BuyEveryBar()
    SingleSymbolEngine(_bars(10), strat).run()
    # on_bar must not fire until i >= WARMUP (5).
    assert min(strat.fired) == 5
    assert strat.fired == [5, 6, 7, 8, 9]


def test_warmup_no_trade_or_position_before_warmup():
    # Only run up to the warm-up boundary: on_bar never fires, so nothing happens.
    strat = _BuyEveryBar()
    result = SingleSymbolEngine(_bars(5), strat).run()
    assert strat.fired == []
    assert result.trades == []
    assert strat.position.size == 0.0


def test_warmup_acts_at_and_after_boundary():
    # buy fires at i=5 (market) -> fills at the open of bar 6.
    strat = _BuyEveryBar()
    SingleSymbolEngine(_bars(8), strat).run()
    assert 5 in strat.fired  # acted at the warm-up boundary
    assert strat.position.size > 0  # the fill went through after the boundary


class _DefaultWarmup(Strategy):
    """No WARMUP set -> default 0 -> fires on every bar (unchanged behavior)."""

    def __init__(self):
        super().__init__()
        self.fired = []

    def on_bar(self, bar):
        self.fired.append(self.index)


def test_default_warmup_is_zero_unchanged():
    strat = _DefaultWarmup()
    assert Strategy.WARMUP == 0
    SingleSymbolEngine(_bars(4), strat).run()
    assert strat.fired == [0, 1, 2, 3]  # fires on every bar, like before
