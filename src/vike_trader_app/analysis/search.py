"""Random + genetic parameter search and multi-objective Pareto fronts.

Complements ``optimizer.grid_search`` (exhaustive) with cheaper/smarter explorers:
- ``random_search``: sample N combos — good baseline, cheap on huge grids.
- ``genetic_search``: evolve a population (tournament select + uniform crossover +
  mutation, with elitism + an evaluation cache) — finds good combos without the full grid.
- ``pareto_front``: non-dominated set for multi-objective trade-offs (e.g. return vs Sharpe).

Deterministic given ``seed`` (stdlib ``random``).
"""

import random

from ..core.engine import BacktestEngine
from .metrics import sharpe
from .optimizer import OptimizeResult


def _scorer(score_fn):
    return score_fn or (lambda res: sharpe(res.equity_curve))


def random_search(bars, make, param_grid, n_samples: int = 50, score_fn=None, fee_rate: float = 0.0, seed: int = 0):
    """Evaluate ``n_samples`` random combos from ``param_grid``, ranked best-first."""
    score_fn = _scorer(score_fn)
    rng = random.Random(seed)
    keys = list(param_grid)
    results: list[OptimizeResult] = []
    for _ in range(n_samples):
        params = {k: rng.choice(param_grid[k]) for k in keys}
        res = BacktestEngine(bars, make(**params), fee_rate=fee_rate).run()
        results.append(OptimizeResult(params=params, score=score_fn(res), result=res))
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def genetic_search(
    bars,
    make,
    param_grid,
    pop_size: int = 20,
    generations: int = 10,
    score_fn=None,
    fee_rate: float = 0.0,
    seed: int = 0,
    mutation_rate: float = 0.2,
):
    """Evolve params toward the optimum; returns every evaluated combo, ranked best-first."""
    score_fn = _scorer(score_fn)
    rng = random.Random(seed)
    keys = list(param_grid)
    cache: dict[tuple, tuple[float, object]] = {}

    def fitness(ind):
        key = tuple(ind[k] for k in keys)
        if key not in cache:
            res = BacktestEngine(bars, make(**ind), fee_rate=fee_rate).run()
            cache[key] = (score_fn(res), res)
        return cache[key][0]

    def random_ind():
        return {k: rng.choice(param_grid[k]) for k in keys}

    pop = [random_ind() for _ in range(pop_size)]
    for _ in range(generations):
        ranked = sorted(pop, key=fitness, reverse=True)
        next_pop = ranked[:2]  # elitism
        while len(next_pop) < pop_size:
            p1 = max(rng.sample(ranked, min(3, len(ranked))), key=fitness)
            p2 = max(rng.sample(ranked, min(3, len(ranked))), key=fitness)
            child = {k: (p1[k] if rng.random() < 0.5 else p2[k]) for k in keys}
            for k in keys:
                if rng.random() < mutation_rate:
                    child[k] = rng.choice(param_grid[k])
            next_pop.append(child)
        pop = next_pop

    results = [
        OptimizeResult(params=dict(zip(keys, key, strict=True)), score=sc, result=res)
        for key, (sc, res) in cache.items()
    ]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def optuna_search(
    bars,
    make,
    param_grid,
    n_trials: int = 50,
    score_fn=None,
    fee_rate: float = 0.0,
    seed: int = 0,
    sampler: str = "tpe",
):
    """Optuna-backed search (TPE/random samplers) — optional ``vike_trader_app[opt]`` extra.

    Smarter than the hand-rolled GA on rugged landscapes (the sampler freqtrade/jesse
    use). Falls back to ``genetic_search``/``random_search`` when optuna isn't installed.
    Returns every evaluated combo as ranked ``OptimizeResult``s.
    """
    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError("optuna_search requires the optional extra: pip install vike_trader_app[opt]") from exc

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    score_fn = _scorer(score_fn)
    keys = list(param_grid)
    samplers = {"tpe": optuna.samplers.TPESampler, "random": optuna.samplers.RandomSampler}
    study = optuna.create_study(direction="maximize", sampler=samplers[sampler](seed=seed))
    cache: dict[tuple, tuple[float, object]] = {}

    def objective(trial):
        params = {k: trial.suggest_categorical(k, param_grid[k]) for k in keys}
        key = tuple(params[k] for k in keys)
        if key not in cache:
            res = BacktestEngine(bars, make(**params), fee_rate=fee_rate).run()
            cache[key] = (score_fn(res), res)
        return cache[key][0]

    study.optimize(objective, n_trials=n_trials)
    results = [
        OptimizeResult(params=dict(zip(keys, key, strict=True)), score=sc, result=res)
        for key, (sc, res) in cache.items()
    ]
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def pareto_front(items, keys, maximize: bool = True):
    """Return the non-dominated subset of ``items`` over the objective ``keys``.

    ``a`` dominates ``b`` if it is >= on every objective and > on at least one
    (signs flipped when ``maximize`` is False).
    """
    sign = 1.0 if maximize else -1.0

    def dominates(a, b):
        ge = all(sign * a[k] >= sign * b[k] for k in keys)
        gt = any(sign * a[k] > sign * b[k] for k in keys)
        return ge and gt

    return [x for x in items if not any(dominates(y, x) for y in items if y is not x)]
