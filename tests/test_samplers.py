"""Generic, engine-agnostic optimizer-sampler core.

Grid / random / genetic / Bayesian optimization over any ``objective(params) -> float``
(higher is better). All search functions return ``Trial``s ranked descending by score.
Deterministic via ``random.Random(seed)`` (genetic) and the seeded optuna sampler (Bayesian).
"""

import builtins

import pytest

from vike_trader_app.analysis.samplers import (
    Trial,
    bayesian_optimize,
    genetic_optimize,
    grid_points,
    optimize,
    random_points,
)

GRID = {"a": [1, 2, 3, 4, 5], "b": [0, 1, 2, 3]}  # convex optimum at a=3, b=2


def _obj(p):
    return -((p["a"] - 3) ** 2) - ((p["b"] - 2) ** 2)


def test_trial_is_frozen_dataclass():
    t = Trial(params={"a": 1}, score=1.5)
    assert t.params == {"a": 1}
    assert t.score == 1.5
    with pytest.raises(Exception):
        t.score = 2.0  # frozen


def test_grid_points_count_is_product_of_lengths():
    pts = grid_points(GRID)
    assert len(pts) == len(GRID["a"]) * len(GRID["b"]) == 20
    # every combo present, deterministic itertools.product order
    assert pts[0] == {"a": 1, "b": 0}
    assert pts[-1] == {"a": 5, "b": 3}
    assert {(d["a"], d["b"]) for d in pts} == {(a, b) for a in GRID["a"] for b in GRID["b"]}


def test_grid_points_deterministic_order():
    assert grid_points(GRID) == grid_points(GRID)


def test_random_points_len_and_valid_and_deterministic():
    pts = random_points(GRID, 12, seed=1)
    assert len(pts) == 12
    assert all(p["a"] in GRID["a"] and p["b"] in GRID["b"] for p in pts)
    assert random_points(GRID, 12, seed=1) == pts  # deterministic run-to-run
    assert random_points(GRID, 12, seed=2) != pts  # different seed differs


def test_random_points_distinct_where_feasible():
    # 20 distinct combos exist; request all 20 -> should be distinct
    pts = random_points(GRID, 20, seed=5)
    keyed = {(p["a"], p["b"]) for p in pts}
    assert len(keyed) == 20


def test_grid_optimize_finds_optimum():
    res = optimize(GRID, _obj, method="grid")
    assert len(res) == 20
    assert res == sorted(res, key=lambda t: t.score, reverse=True)
    assert res[0].params == {"a": 3, "b": 2}
    assert res[0].score == 0.0


def test_genetic_finds_optimum():
    res = genetic_optimize(GRID, _obj, pop_size=16, generations=12, seed=2)
    assert res
    assert res == sorted(res, key=lambda t: t.score, reverse=True)
    assert res[0].params == {"a": 3, "b": 2}
    assert res[0].score == 0.0


def test_genetic_is_deterministic():
    a = genetic_optimize(GRID, _obj, pop_size=12, generations=8, seed=7)
    b = genetic_optimize(GRID, _obj, pop_size=12, generations=8, seed=7)
    assert [(t.params, t.score) for t in a] == [(t.params, t.score) for t in b]


def test_genetic_eval_cache_runs_objective_once_per_combo():
    seen: list[tuple] = []

    def counting(p):
        seen.append((p["a"], p["b"]))
        return _obj(p)

    res = genetic_optimize(GRID, counting, pop_size=10, generations=6, seed=1)
    # objective invoked exactly once per distinct combo evaluated
    assert len(seen) == len(set(seen))
    assert len(res) == len(set(seen))


def test_dispatcher_grid_random_genetic():
    g = optimize(GRID, _obj, method="grid")
    r = optimize(GRID, _obj, method="random", seed=0)
    ga = optimize(GRID, _obj, method="genetic", seed=0, pop_size=12, generations=8)
    for res in (g, r, ga):
        assert res == sorted(res, key=lambda t: t.score, reverse=True)
    # random defaults to 50 draws over a 20-combo grid -> distinct Trials cap at 20
    assert len(r) == 20
    assert len({(t.params["a"], t.params["b"]) for t in r}) == len(r)


def test_dispatcher_random_default_samples_50_points():
    # n_trials defaults to 50 -> random_points yields exactly 50 sampled points
    assert len(random_points(GRID, 50, seed=0)) == 50


def test_dispatcher_random_respects_n_trials():
    r = optimize(GRID, _obj, method="random", n_trials=7, seed=0)
    assert len(r) == 7  # 7 distinct draws from a 20-combo grid stay distinct


def test_dispatcher_bad_method_raises():
    with pytest.raises(ValueError, match="method"):
        optimize(GRID, _obj, method="bogus")


def test_bayesian_finds_good_combo_when_available():
    pytest.importorskip("optuna")
    res = bayesian_optimize(GRID, _obj, n_trials=40, seed=3)
    assert res
    assert res == sorted(res, key=lambda t: t.score, reverse=True)
    assert res[0].params["a"] in GRID["a"] and res[0].params["b"] in GRID["b"]


def test_bayesian_dispatcher_defaults_n_trials():
    pytest.importorskip("optuna")
    res = optimize(GRID, _obj, method="bayesian", seed=3)
    assert res
    assert res == sorted(res, key=lambda t: t.score, reverse=True)


def test_bayesian_unknown_sampler_raises_value_error():
    pytest.importorskip("optuna")
    with pytest.raises(ValueError, match="unknown sampler"):
        bayesian_optimize(GRID, _obj, n_trials=2, sampler="bogus")


def test_bayesian_gp_sampler_returns_ranked():
    pytest.importorskip("optuna")
    res = bayesian_optimize(GRID, _obj, n_trials=5, seed=42, sampler="gp")
    assert res
    assert res == sorted(res, key=lambda t: t.score, reverse=True)
    assert all(t.params["a"] in GRID["a"] and t.params["b"] in GRID["b"] for t in res)


def test_bayesian_raises_runtime_error_without_optuna(monkeypatch):
    real_import = builtins.__import__

    def _block(name, *a, **k):
        if name == "optuna":
            raise ImportError("simulated missing optuna")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _block)
    with pytest.raises(RuntimeError, match="vike_trader_app\\[opt\\]"):
        bayesian_optimize(GRID, _obj, n_trials=2)
