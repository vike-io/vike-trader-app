"""Generic, engine-agnostic optimizer-sampler core.

Run grid / random / genetic / Bayesian optimization over **any** objective
``Callable[[dict], float]`` (higher is better). This extracts the proven
algorithms from :mod:`vike_trader_app.analysis.search` (which are hardwired to
``SingleSymbolEngine``) into a reusable form that knows nothing about strategies,
bars, or backtests — it only ever calls ``objective(params)``.

All search functions return a list of :class:`Trial` ranked **descending** by
score. Determinism: genetic uses stdlib ``random.Random(seed)``; Bayesian seeds
the optuna sampler.

``param_grid`` is a ``dict[str, list]`` — each parameter draws from a finite,
categorical list of candidate values (matching the grid-search convention).
"""

import itertools
import random
from collections.abc import Callable
from dataclasses import dataclass

Objective = Callable[[dict], float]


@dataclass(frozen=True)
class Trial:
    """One evaluated parameter combination and its objective score."""

    params: dict
    score: float


def _key(params: dict) -> tuple:
    """Stable, hashable cache key independent of dict insertion order."""
    return tuple(sorted(params.items()))


def _ranked(cache: dict[tuple, tuple[dict, float]]) -> list[Trial]:
    """Build Trials from an eval cache, ranked descending by score."""
    trials = [Trial(params=params, score=score) for params, score in cache.values()]
    trials.sort(key=lambda t: t.score, reverse=True)
    return trials


def grid_points(param_grid: dict) -> list[dict]:
    """Every combination in ``param_grid`` as dicts, in deterministic order.

    Cartesian product (``itertools.product``) over the dict-of-lists, in key
    order — so the first key varies slowest and the last key varies fastest.
    """
    keys = list(param_grid)
    return [dict(zip(keys, combo, strict=True)) for combo in itertools.product(*(param_grid[k] for k in keys))]


def random_points(param_grid: dict, n: int, *, seed: int = 0) -> list[dict]:
    """Sample ``n`` random combos from ``param_grid``, distinct where feasible.

    Deterministic given ``seed`` (stdlib ``random.Random``). When ``n`` does not
    exceed the number of distinct combinations the result is de-duplicated; once
    the space is exhausted it falls back to (possibly repeated) random draws so
    that exactly ``n`` points are always returned.
    """
    rng = random.Random(seed)
    keys = list(param_grid)
    total = 1
    for k in keys:
        total *= len(param_grid[k])

    points: list[dict] = []
    seen: set[tuple] = set()
    distinct_target = min(n, total)
    # Phase 1: draw distinct combos until we hit the feasible-distinct target.
    while len(points) < distinct_target:
        params = {k: rng.choice(param_grid[k]) for k in keys}
        key = _key(params)
        if key not in seen:
            seen.add(key)
            points.append(params)
    # Phase 2: top up with further random draws (only when n > total).
    while len(points) < n:
        points.append({k: rng.choice(param_grid[k]) for k in keys})
    return points


def genetic_optimize(
    param_grid: dict,
    objective: Objective,
    *,
    pop_size: int = 20,
    generations: int = 10,
    seed: int = 0,
    mutation_rate: float = 0.2,
) -> list[Trial]:
    """Evolve combos toward the optimum over the categorical ``param_grid``.

    Tournament selection (size 3) + uniform crossover + per-gene mutation, with
    2-elitism and an evaluation cache so ``objective`` runs **once per distinct
    combo**. Deterministic given ``seed``. Returns every evaluated distinct combo
    ranked best-first — on a convex objective it finds the grid optimum.
    """
    rng = random.Random(seed)
    keys = list(param_grid)
    cache: dict[tuple, tuple[dict, float]] = {}

    def fitness(ind: dict) -> float:
        key = _key(ind)
        if key not in cache:
            cache[key] = (ind, objective(ind))
        return cache[key][1]

    def random_ind() -> dict:
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

    return _ranked(cache)


def bayesian_optimize(
    param_grid: dict,
    objective: Objective,
    *,
    n_trials: int = 50,
    seed: int = 0,
    sampler: str = "tpe",
) -> list[Trial]:
    """Optuna-backed optimization over the categorical ``param_grid``.

    Requires the optional ``vike_trader_app[opt]`` extra (optuna). ``sampler``:

    - ``"tpe"``    — Tree-structured Parzen Estimator (default).
    - ``"random"`` — Pure random search (reproducible baseline).
    - ``"gp"``     — Gaussian-Process Bayesian optimization (``GPSampler``).
    - ``"cmaes"``  — CMA-ES (falls back to independent sampling for categoricals).

    Each trial suggests every param via ``suggest_categorical``; an evaluation
    cache keeps ``objective`` to once per distinct combo. Returns ranked Trials.
    """
    try:
        import optuna
    except ImportError as exc:
        raise RuntimeError(
            "bayesian_optimize requires the optional extra: pip install vike_trader_app[opt]"
        ) from exc

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    keys = list(param_grid)
    samplers = {
        "tpe": optuna.samplers.TPESampler,
        "random": optuna.samplers.RandomSampler,
        "gp": optuna.samplers.GPSampler,
        "cmaes": optuna.samplers.CmaEsSampler,
    }
    if sampler not in samplers:
        raise ValueError(f"unknown sampler {sampler!r}; expected one of {sorted(samplers)}")

    study = optuna.create_study(direction="maximize", sampler=samplers[sampler](seed=seed))
    cache: dict[tuple, tuple[dict, float]] = {}

    def _trial_objective(trial) -> float:
        params = {k: trial.suggest_categorical(k, param_grid[k]) for k in keys}
        key = _key(params)
        if key not in cache:
            cache[key] = (params, objective(params))
        return cache[key][1]

    study.optimize(_trial_objective, n_trials=n_trials)
    return _ranked(cache)


def optimize(
    param_grid: dict,
    objective: Objective,
    *,
    method: str = "grid",
    seed: int = 0,
    n_trials: int | None = None,
    pop_size: int = 20,
    generations: int = 10,
    mutation_rate: float = 0.2,
    sampler: str = "tpe",
) -> list[Trial]:
    """Dispatch to a search ``method`` and return ranked Trials.

    ``method`` is one of ``{"grid", "random", "genetic", "bayesian"}``. ``grid``
    evaluates every :func:`grid_points` combo; ``random`` samples
    :func:`random_points`; both ``random`` and ``bayesian`` default ``n_trials``
    to 50 when ``None``. Raises ``ValueError`` for an unknown method or a param whose
    candidate-value list is empty (which would otherwise yield zero trials).
    """
    empty = [k for k, vs in param_grid.items() if not vs]
    if empty:
        raise ValueError(f"param_grid has no candidate values for: {empty}")
    if method == "grid":
        cache: dict[tuple, tuple[dict, float]] = {}
        for params in grid_points(param_grid):
            cache[_key(params)] = (params, objective(params))
        return _ranked(cache)

    if method == "random":
        trials_n = 50 if n_trials is None else n_trials
        cache = {}
        for params in random_points(param_grid, trials_n, seed=seed):
            key = _key(params)
            if key not in cache:
                cache[key] = (params, objective(params))
        return _ranked(cache)

    if method == "genetic":
        return genetic_optimize(
            param_grid,
            objective,
            pop_size=pop_size,
            generations=generations,
            seed=seed,
            mutation_rate=mutation_rate,
        )

    if method == "bayesian":
        trials_n = 50 if n_trials is None else n_trials
        return bayesian_optimize(param_grid, objective, n_trials=trials_n, seed=seed, sampler=sampler)

    raise ValueError(f"unknown method {method!r}; expected one of ['bayesian', 'genetic', 'grid', 'random']")
