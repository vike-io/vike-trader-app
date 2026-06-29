"""Grid-search optimizer tests."""

from vike_trader_app.analysis.optimizer import grid_search
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _rising_bars(n=6):
    return [
        Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0)
        for i in range(n)
    ]


def _make(size):
    class _S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(size)

    return _S()


def test_grid_search_returns_one_result_per_combo():
    results = grid_search(
        _rising_bars(), _make, {"size": [0.001, 0.01, 0.1]}, score_fn=lambda r: r.final_equity
    )
    assert len(results) == 3


def test_grid_search_sorted_best_first():
    results = grid_search(
        _rising_bars(), _make, {"size": [0.001, 0.01, 0.1]}, score_fn=lambda r: r.final_equity
    )
    # on a rising market, larger size -> larger final equity -> ranked first
    assert results[0].params["size"] == 0.1
    assert results[-1].params["size"] == 0.001
    assert results[0].score >= results[-1].score


def test_grid_search_cartesian_product_of_two_params():
    results = grid_search(
        _rising_bars(),
        lambda a, b: _make(a),  # noqa: ARG005
        {"a": [0.01, 0.02], "b": [1, 2, 3]},
        score_fn=lambda r: r.final_equity,
    )
    assert len(results) == 6
    assert all("a" in r.params and "b" in r.params for r in results)
