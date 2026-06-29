"""Parallel grid optimization must be bit-identical to the serial path (same engine, same combos).

The parallel path *source-ships* the strategy to worker processes and pre-fills the optimize cache;
these tests pin that it produces exactly the serial numbers, so the speedup never changes results.
"""

import math

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy_loader import load_strategy_from_string
from vike_trader_app.tester import StrategyTester, TesterConfig
from vike_trader_app.tester.parallel import grid_combos, resolve_workers

# A self-contained SMA-cross strategy as SOURCE TEXT — this is what gets shipped to the workers
# (mirrors how the Studio hands its editor text to the optimizer).
_SOURCE = '''from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


class SmaX(Strategy):
    WARMUP = 30
    fast = 5
    slow = 20
    PARAM_GRID = {"fast": [3, 5, 8], "slow": [15, 20, 30]}

    def __init__(self):
        self.closes = []

    def on_bar(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) <= self.slow:
            return
        f = sum(self.closes[-self.fast:]) / self.fast
        s = sum(self.closes[-self.slow:]) / self.slow
        fp = sum(self.closes[-self.fast - 1:-1]) / self.fast
        sp = sum(self.closes[-self.slow - 1:-1]) / self.slow
        if fp <= sp and f > s and self.position.size == 0:
            self.buy(1.0)
        elif fp >= sp and f < s and self.position.size > 0:
            self.close()
'''


def _bars(n=480):
    """Deterministic wavy-with-drift closes so the SMA combos actually differ in score."""
    out = []
    for i in range(n):
        c = round(100 + 25 * math.sin(i / 11.0) + 12 * math.sin(i / 47.0) + i * 0.02, 2)
        out.append(Bar(ts=i * 60_000, open=c, high=c, low=c, close=c, volume=1.0))
    return out


def _score_map(report):
    """{sorted-params-tuple: rounded score} — order-independent, tie-safe equality key."""
    return {tuple(sorted(t.params.items())): round(t.score, 9) for t in report.ranked}


def test_resolve_workers_auto_and_clamp():
    import os

    from vike_trader_app.tester.parallel import _AUTO_WORKER_CAP

    cpu = os.cpu_count() or 1
    auto = min(cpu, _AUTO_WORKER_CAP)
    assert resolve_workers(0) == auto         # 0 => Auto (spawn-cost-aware cap, not all cores)
    assert resolve_workers(None) == auto      # unset => Auto
    assert resolve_workers(-4) == auto        # negative => Auto
    assert resolve_workers(1) == 1
    assert resolve_workers(10_000) == cpu     # explicit value clamped to the core count


def test_grid_combos_is_full_product():
    combos = grid_combos({"fast": [3, 5], "slow": [15, 20]})
    assert {tuple(sorted(c.items())) for c in combos} == {
        (("fast", 3), ("slow", 15)), (("fast", 3), ("slow", 20)),
        (("fast", 5), ("slow", 15)), (("fast", 5), ("slow", 20)),
    }


@pytest.mark.parametrize("workers", [2, 4])
def test_parallel_grid_matches_serial(workers):
    cls = load_strategy_from_string(_SOURCE)
    bars = _bars()
    cfg = TesterConfig()

    serial = StrategyTester(cls(), bars, cfg).optimize(
        cls.make, cls.PARAM_GRID, method="grid", workers=1)
    parallel = StrategyTester(cls(), bars, cfg).optimize(
        cls.make, cls.PARAM_GRID, method="grid", workers=workers, strategy_source=_SOURCE)

    assert serial.n_trials == parallel.n_trials
    assert _score_map(serial) == _score_map(parallel)      # every combo scores identically
    assert serial.best.params == parallel.best.params      # (no ties in this grid)


def _returns_map(pairs):
    return {tuple(sorted(p.items())): round(rep.total_return, 9) for p, rep in pairs}


def test_gridpool_matches_serial():
    """A reused GridPool (parallel) returns the same per-combo numbers as its own serial loop."""
    from vike_trader_app.tester.parallel import GridPool

    bars = _bars()
    cfg = TesterConfig()
    with GridPool(_SOURCE, 1) as serial:        # workers=1 -> in-process loop
        smap = _returns_map(serial.run(GRID := {"fast": [5, 8, 10, 12, 15, 20], "slow": [25, 30, 40, 50, 60, 80]}, bars, cfg))
    with GridPool(_SOURCE, 4) as parallel:
        pmap = _returns_map(parallel.run(GRID, bars, cfg))
    assert smap == pmap


def test_gridpool_reused_across_datasets():
    """ONE pool, run on two DIFFERENT datasets (the walk-forward case): each result is correct and
    they genuinely differ — proving the pool reuses workers without leaking the prior window's data."""
    from vike_trader_app.tester.parallel import GridPool

    cfg = TesterConfig()
    grid = {"fast": [5, 8, 10, 12, 15, 20], "slow": [25, 30, 40, 50, 60, 80]}
    a, b = _bars()[:300], _bars()[120:]
    with GridPool(_SOURCE, 4) as pool:          # reused across both datasets
        ra, rb = _returns_map(pool.run(grid, a, cfg)), _returns_map(pool.run(grid, b, cfg))
    with GridPool(_SOURCE, 1) as s:             # serial reference
        sa, sb = _returns_map(s.run(grid, a, cfg)), _returns_map(s.run(grid, b, cfg))
    assert ra == sa and rb == sb                # reused pool correct on BOTH windows
    assert ra != rb                             # and the two windows really are different data


def test_walk_forward_parallel_matches_serial():
    cls = load_strategy_from_string(_SOURCE)
    bars = _bars()
    cfg = TesterConfig()

    wf1 = StrategyTester(cls(), bars, cfg).walk_forward(
        cls.make, cls.PARAM_GRID, method="grid", n_splits=3, workers=1)
    wf2 = StrategyTester(cls(), bars, cfg).walk_forward(
        cls.make, cls.PARAM_GRID, method="grid", n_splits=3, workers=4, strategy_source=_SOURCE)

    assert wf1.n_windows == wf2.n_windows
    assert [w.best_params for w in wf1.windows] == [w.best_params for w in wf2.windows]
    assert abs(wf1.oos_report.total_return - wf2.oos_report.total_return) < 1e-9
