"""Parallel (multiprocessing) grid search matches the sequential result.

The strategy class and score fn are module-level so they pickle under spawn.
"""

from vike_trader_app.analysis.optimizer import grid_search
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


class PStrat(Strategy):
    size = 1.0

    def on_bar(self, bar):
        if self.index == 0:
            self.buy(self.size)


def _bars():
    return [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(12)]


def _final_equity(res):
    return res.final_equity


def test_parallel_grid_matches_sequential():
    grid = {"size": [0.1, 0.5, 1.0, 2.0]}
    seq = grid_search(_bars(), PStrat.make, grid, score_fn=_final_equity, workers=1)
    par = grid_search(_bars(), PStrat.make, grid, score_fn=_final_equity, workers=2)
    assert [(r.params, round(r.score, 6)) for r in seq] == [(r.params, round(r.score, 6)) for r in par]
    assert par[0].params["size"] == 2.0  # largest on a rising tape
