"""Random + genetic search and multi-objective Pareto front."""

from vike_trader_app.analysis.optimizer import grid_search
import pytest

from vike_trader_app.analysis.search import genetic_search, optuna_search, pareto_front, random_search
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _rising(n=12):
    return [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1.0) for i in range(n)]


def _make(fast, slow):  # noqa: ARG001 - 'slow' just widens the search space
    class _S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(fast)  # bigger 'fast' -> bigger position -> bigger equity on a rising tape

    return _S()


GRID = {"fast": [0.1, 0.5, 1.0, 2.0], "slow": [10, 20, 30, 40]}
SCORE = lambda r: r.final_equity  # noqa: E731


def test_random_search_returns_n_samples_with_valid_params():
    res = random_search(_rising(), _make, GRID, n_samples=15, score_fn=SCORE, seed=1)
    assert len(res) == 15
    assert all(r.params["fast"] in GRID["fast"] and r.params["slow"] in GRID["slow"] for r in res)
    assert res == sorted(res, key=lambda r: r.score, reverse=True)


def test_genetic_search_finds_global_optimum():
    ga = genetic_search(_rising(), _make, GRID, pop_size=12, generations=8, score_fn=SCORE, seed=2)
    grid = grid_search(_rising(), _make, GRID, score_fn=SCORE)
    # GA explores enough of the 16-combo space to match the exhaustive optimum
    assert ga[0].score == grid[0].score
    assert ga[0].params["fast"] == 2.0  # largest position wins on a rising tape


def test_pareto_front_keeps_non_dominated():
    items = [
        {"name": "A", "ret": 1.0, "sharpe": 3.0},
        {"name": "B", "ret": 2.0, "sharpe": 2.0},
        {"name": "C", "ret": 3.0, "sharpe": 1.0},
        {"name": "D", "ret": 1.0, "sharpe": 1.0},  # dominated by A and C
    ]
    front = pareto_front(items, ["ret", "sharpe"])
    names = {x["name"] for x in front}
    assert names == {"A", "B", "C"}


def test_optuna_backend_finds_good_combo_when_available():
    pytest.importorskip("optuna")
    res = optuna_search(_rising(), _make, GRID, n_trials=20, score_fn=SCORE, seed=3)
    assert res  # non-empty, ranked
    assert res == sorted(res, key=lambda r: r.score, reverse=True)
    assert res[0].params["fast"] == 2.0  # largest position wins on a rising tape


def test_optuna_search_raises_without_extra(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _block(name, *a, **k):
        if name == "optuna":
            raise ImportError("simulated missing optuna")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block)
    with pytest.raises(RuntimeError, match="vike_trader_app\\[opt\\]"):
        optuna_search(_rising(), _make, GRID, n_trials=2, score_fn=SCORE)
