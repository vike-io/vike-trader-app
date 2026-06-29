"""Parameter optimization via grid search over the backtest engine.

``make(**params)`` builds a fresh Strategy for each parameter combination; each is
run through the engine and scored. Results are returned ranked best-first.
"""

import concurrent.futures as cf
import itertools
from dataclasses import dataclass, field

from ..core.engine import SingleSymbolEngine
from .metrics import sharpe


@dataclass
class OptimizeResult:
    """One parameter combination and its score."""

    params: dict
    score: float
    result: object = field(default=None, repr=False)


def _default_score(res):  # module-level so it pickles for multiprocessing
    return sharpe(res.equity_curve)


def _eval_combo(args):
    """Worker: run one combo and return (params, score). Top-level for picklability."""
    bars, make, params, score_fn, fee_rate = args
    res = SingleSymbolEngine(bars, make(**params), fee_rate=fee_rate).run()
    return params, score_fn(res)


def grid_search(bars, make, param_grid: dict, score_fn=None, fee_rate: float = 0.0, workers: int = 1):
    """Run every combination in ``param_grid`` and rank by ``score_fn`` (default: Sharpe).

    ``make`` is a callable ``(**params) -> Strategy``. ``score_fn`` takes the engine
    ``Result`` and returns a float (higher is better). ``workers > 1`` runs combos
    across processes (combos are independent) — ``make``/``score_fn``/strategy must be
    picklable (module-level), and per-combo ``Result`` objects are not returned.
    """
    score_fn = score_fn or _default_score
    keys = list(param_grid)
    combos = [dict(zip(keys, c, strict=True)) for c in itertools.product(*(param_grid[k] for k in keys))]

    if workers and workers > 1:
        packed = [(bars, make, p, score_fn, fee_rate) for p in combos]
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            scored = list(ex.map(_eval_combo, packed))
        results = [OptimizeResult(params=p, score=s, result=None) for p, s in scored]
    else:
        results = []
        for params in combos:
            res = SingleSymbolEngine(bars, make(**params), fee_rate=fee_rate).run()
            results.append(OptimizeResult(params=params, score=score_fn(res), result=res))

    results.sort(key=lambda r: r.score, reverse=True)
    return results
