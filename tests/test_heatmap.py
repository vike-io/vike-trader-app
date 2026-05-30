"""Two-parameter optimization heatmap-data tests."""

import pytest

from vike_trader_app.analysis.heatmap import heatmap_grid
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _rising(n=8):
    return [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(n)]


def _make(size, hold):  # noqa: ARG001 - 'hold' just widens the grid
    class _S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(size)

    return _S()


def test_heatmap_grid_shape_and_values():
    bars = _rising()
    hm = heatmap_grid(
        bars,
        _make,
        param_x="size",
        values_x=[0.1, 1.0],
        param_y="hold",
        values_y=[1, 2, 3],
        score_fn=lambda r: r.final_equity,
    )
    assert len(hm.scores) == 3          # rows = y values
    assert all(len(row) == 2 for row in hm.scores)  # cols = x values
    assert hm.values_x == [0.1, 1.0] and hm.values_y == [1, 2, 3]
    # rising market: larger size -> higher final equity in every row
    assert all(row[1] >= row[0] for row in hm.scores)
