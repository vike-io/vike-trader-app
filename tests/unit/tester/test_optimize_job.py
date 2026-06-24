"""The Qt-free optimize-job seam: walk-forward + the optimization-surface grid sweep.

Pins the bug fix — the surface sweep MUST run on the worker pool (``workers`` + ``strategy_source``,
the parallel path), not serially in-process — plus the skip/failure fallbacks that keep the
walk-forward result intact when the surface is ineligible or blows up.
"""

import math
from types import SimpleNamespace

from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy_loader import load_strategy_from_string
from vike_trader_app.tester import StrategyTester, TesterConfig
from vike_trader_app.tester.optimize_job import (
    SURFACE_MAX_COMBOS,
    OptimizeJobResult,
    _surface_eligible,
    run_optimize_job,
)

# A self-contained SMA-cross strategy as SOURCE TEXT — mirrors how the Studio ships its editor text
# to the optimizer workers (same idiom as tests/unit/tester/test_parallel_optimize.py).
_SOURCE = '''from vike_trader_app.core.strategy import Strategy


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


def _wf_kwargs(**over):
    """asdict(OptimizerConfig) shape — the keys ARE walk_forward kwargs. workers=1 keeps tests fast."""
    kw = dict(method="grid", criterion="sharpe", mode="anchored", n_splits=3, n_trials=50,
              pop_size=20, generations=10, sampler="tpe", seed=0, workers=1)
    kw.update(over)
    return kw


def test_surface_eligible_thresholds():
    assert _surface_eligible({"a": [1, 2], "b": [3, 4]}) is True
    assert _surface_eligible({"a": [1, 2], "b": [3]}) is False         # only one multi-valued axis
    big = list(range(21))                                              # 21*21 = 441 > the cap
    assert SURFACE_MAX_COMBOS == 400
    assert _surface_eligible({"a": big, "b": big}) is False


def test_single_symbol_job_populates_wf_and_surface():
    cls = load_strategy_from_string(_SOURCE)
    bars = _bars()
    res = run_optimize_job(strategy_cls=cls, grid=cls.PARAM_GRID, config=TesterConfig(),
                           wf_kwargs=_wf_kwargs(), strategy_source=_SOURCE, bars=bars)
    assert isinstance(res, OptimizeJobResult)
    assert res.is_portfolio is False
    assert res.chart_bars is bars
    assert res.criterion == "sharpe"
    assert res.wf.n_windows == 3
    assert res.surface_ranked is not None and len(res.surface_ranked) == 9   # full 3x3 grid
    assert set(res.best_params) == {"fast", "slow"}
    assert isinstance(res.overfit_level, str)


def test_surface_sweep_runs_on_the_worker_pool(monkeypatch):
    """THE FIX: the surface optimize must receive workers + strategy_source (the parallel path),
    not the serial default. Spy the optimize call; stub walk_forward so only the surface call shows."""
    cls = load_strategy_from_string(_SOURCE)
    captured: dict = {}

    def fake_optimize(self, make, grid, **kw):
        captured.update(kw)
        return SimpleNamespace(ranked=[SimpleNamespace(params={"fast": 5, "slow": 20}, score=1.0)])

    def fake_wf(self, make, grid, **kw):
        return SimpleNamespace(n_windows=0, windows=[], oos_report=SimpleNamespace(verdict=None))

    monkeypatch.setattr(StrategyTester, "optimize", fake_optimize)
    monkeypatch.setattr(StrategyTester, "walk_forward", fake_wf)

    run_optimize_job(strategy_cls=cls, grid=cls.PARAM_GRID, config=TesterConfig(),
                     wf_kwargs=_wf_kwargs(workers=4), strategy_source=_SOURCE, bars=_bars())

    assert captured.get("workers") == 4
    assert captured.get("strategy_source") == _SOURCE
    assert captured.get("method") == "grid"
    assert captured.get("criterion") == "sharpe"


def test_surface_skipped_when_grid_has_one_axis():
    cls = load_strategy_from_string(_SOURCE)
    grid = {"fast": [3, 5, 8], "slow": [20]}     # only one multi-valued axis -> no surface
    res = run_optimize_job(strategy_cls=cls, grid=grid, config=TesterConfig(),
                           wf_kwargs=_wf_kwargs(), strategy_source=_SOURCE, bars=_bars())
    assert res.surface_ranked is None
    assert res.wf.n_windows == 3                  # WF still produced


def test_surface_failure_does_not_sink_wf(monkeypatch):
    """A surface sweep blow-up is swallowed; the walk-forward result still comes back."""
    cls = load_strategy_from_string(_SOURCE)
    fake_wf = SimpleNamespace(
        n_windows=3, windows=[SimpleNamespace(best_params={"fast": 5, "slow": 20})],
        oos_report=SimpleNamespace(verdict=None),
    )

    def boom(self, make, grid, **kw):
        raise RuntimeError("surface boom")

    monkeypatch.setattr(StrategyTester, "walk_forward", lambda self, *a, **k: fake_wf)
    monkeypatch.setattr(StrategyTester, "optimize", boom)

    res = run_optimize_job(strategy_cls=cls, grid=cls.PARAM_GRID, config=TesterConfig(),
                           wf_kwargs=_wf_kwargs(), strategy_source=_SOURCE, bars=_bars())
    assert res.wf is fake_wf
    assert res.surface_ranked is None             # error swallowed, WF intact
    assert res.best_params == {"fast": 5, "slow": 20}


def test_portfolio_job_uses_portfolio_tester(monkeypatch):
    cls = load_strategy_from_string(_SOURCE)
    fake_wf = SimpleNamespace(n_windows=2, windows=[SimpleNamespace(best_params={})],
                              oos_report=SimpleNamespace(verdict=None))

    class FakePT:
        def __init__(self, bars, config, ranges=None):
            self.bars = bars

        def walk_forward(self, make, grid, **kw):
            return fake_wf

        def optimize(self, make, grid, **kw):
            return SimpleNamespace(ranked=[SimpleNamespace(params={"fast": 5, "slow": 20}, score=1.0)])

    import vike_trader_app.tester.portfolio_tester as pt_mod
    monkeypatch.setattr(pt_mod, "PortfolioStrategyTester", FakePT)

    res = run_optimize_job(strategy_cls=cls, grid=cls.PARAM_GRID, config=TesterConfig(),
                           wf_kwargs=_wf_kwargs(), strategy_source=_SOURCE,
                           portfolio_bars={"AAA": _bars()}, portfolio_name="MyPort")
    assert res.is_portfolio is True
    assert res.chart_bars == []
    assert res.portfolio_name == "MyPort"
    assert res.wf is fake_wf
