"""Parallel grid evaluation for the optimizer — run independent param combos across worker
processes, *source-shipping* the strategy so the Studio's exec'd (unpicklable) classes work.

Only the exhaustive ``grid`` method parallelizes — combos are independent, so each is one full
``Backtester`` run. ``random`` / ``genetic`` / ``bayesian`` are sequential by construction (each
trial depends on the previous scores) and stay single-process upstream.

Design notes:
- Each worker compiles the strategy SOURCE once (``initializer``) and keeps the bar data + config,
  so a task carries only the small param dict — the bars cross the process boundary once per worker,
  not once per combo.
- The per-combo call is byte-identical to the serial ``StrategyTester._run_trial``
  (``Backtester(make(**params), data, config).run()``), so the parallel numbers MATCH the serial
  path exactly — this is the same engine, just spread across cores.
- Pure-stdlib + tester-only imports (no Qt), so a spawned child stays light and never re-launches
  the GUI.
"""

from __future__ import annotations

import concurrent.futures as cf
import itertools
import os

#: Per-worker scratch, populated by the pool ``initializer`` (one compile + data hold per process).
_WORKER: dict = {}

#: Below this many combos, process-pool startup costs more than the serial loop saves, so we stay
#: in-process regardless of the requested worker count.
_MIN_PARALLEL_COMBOS = 4

#: "Auto" worker cap. MEASURED on Windows (spawn): a 36-combo sweep hit ~6.3x at 4 workers and ~5.8x
#: at 8, but only ~4.2x at 16/32 — process-spawn + per-worker import cost overwhelms the compute
#: saved once workers climb past the sweet spot. So Auto defaults to a spawn-cost-aware cap, NOT all
#: cores; power users can still set an explicit higher count (clamped only to the core count).
_AUTO_WORKER_CAP = 8


def resolve_workers(workers: int | None) -> int:
    """Resolve a requested worker count to a concrete process count.

    ``0``/``None``/negative -> "Auto" = ``min(cores, _AUTO_WORKER_CAP)`` (a spawn-cost-aware default,
    measured to beat "all cores" on Windows). An explicit value is clamped to ``[1, os.cpu_count()]``
    so a user can never request more processes than the machine has cores.
    """
    cpu = os.cpu_count() or 1
    if not workers or workers <= 0:
        return min(cpu, _AUTO_WORKER_CAP)
    return max(1, min(int(workers), cpu))


def grid_combos(param_grid: dict) -> list[dict]:
    """Every parameter combination in ``param_grid`` as a list of dicts (Cartesian product)."""
    keys = list(param_grid)
    return [dict(zip(keys, c, strict=True))
            for c in itertools.product(*(param_grid[k] for k in keys))]


def _init_worker(source: str, data, config) -> None:
    """Pool initializer: compile the strategy source ONCE and hold the bars + config per process."""
    from ..core.strategy_loader import load_strategy_from_string

    # The parent already ran the AST pre-flight gate before optimizing; skip the re-check here
    # (the same source is exec'd in every worker regardless — same trust boundary as the parent).
    _WORKER["cls"] = load_strategy_from_string(source, validate=False)
    _WORKER["data"] = data
    _WORKER["config"] = config


def _run_params(params: dict):
    """Worker task: build the strategy for ``params`` and run one backtest -> (params, report)."""
    from .backtester import Backtester

    rep = Backtester(_WORKER["cls"].make(**params), _WORKER["data"], _WORKER["config"]).run()
    return params, rep


def parallel_grid_reports(source: str, param_grid: dict, data, config, workers: int | None):
    """Yield ``(params, TesterReport)`` for every grid combo, computed across ``workers`` processes.

    Falls back to a single in-process loop when only one worker is resolved or there is a single
    combo (so the caller gets one uniform code path). The strategy is identified only by ``source``
    text — that is what makes a dynamically-exec'd Studio strategy usable across processes.
    """
    combos = grid_combos(param_grid)
    n = min(resolve_workers(workers), len(combos))  # never more processes than combos

    if n <= 1 or len(combos) < _MIN_PARALLEL_COMBOS:
        from ..core.strategy_loader import load_strategy_from_string
        from .backtester import Backtester

        cls = load_strategy_from_string(source, validate=False)
        for p in combos:
            yield p, Backtester(cls.make(**p), data, config).run()
        return

    with cf.ProcessPoolExecutor(
        max_workers=n, initializer=_init_worker, initargs=(source, data, config)
    ) as ex:
        yield from ex.map(_run_params, combos)
