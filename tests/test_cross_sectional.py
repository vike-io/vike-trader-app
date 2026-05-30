"""Cross-sectional top-k rotation: rank the universe each rebalance, hold the best k."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import CrossSectionalStrategy, PortfolioEngine


def _series(opens):
    return [Bar(ts=i * 60_000, open=o, high=o + 1, low=o - 1, close=o, volume=1.0) for i, o in enumerate(opens)]


class Momentum(CrossSectionalStrategy):
    k = 2
    rebalance_every = 1
    lookback = 3

    def score(self, symbol, history):
        if len(history) <= self.lookback:
            return None
        return history[-1] / history[-1 - self.lookback] - 1.0  # trailing return


def test_top_k_holds_strongest_drops_weakest():
    # A rises fastest, B medium, C falls -> top-2 momentum = {A, B}; C never held
    bars = {
        "A": _series([100, 105, 112, 121, 132, 145, 160, 177]),
        "B": _series([100, 101, 103, 105, 108, 111, 114, 118]),
        "C": _series([100, 99, 98, 97, 96, 95, 94, 93]),
    }
    eng = PortfolioEngine(bars, Momentum(), cash=100_000.0)
    eng.run()
    assert eng.position_of("A").size > 0
    assert eng.position_of("B").size > 0
    assert eng.position_of("C").size == pytest.approx(0.0)  # rotated out


class _RebalanceEvery3(CrossSectionalStrategy):
    k = 1
    rebalance_every = 3
    lookback = 1

    def __init__(self):
        super().__init__()
        self.rebalanced_on = []

    def score(self, symbol, history):
        if len(history) <= self.lookback:
            return None
        self.rebalanced_on.append(self.index)
        return history[-1]

    def weights(self, winners):
        return {winners[0]: 1.0}


def test_rebalance_cadence_only_fires_on_schedule():
    bars = {"A": _series([100] * 9), "B": _series([101] * 9)}
    strat = _RebalanceEvery3()
    PortfolioEngine(bars, strat, cash=10_000.0).run()
    # score() only runs on indices divisible by rebalance_every (0,3,6) and after warmup
    assert set(strat.rebalanced_on) <= {0, 3, 6}
