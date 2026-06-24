"""Qt-free 'run an optimize job' seam: a walk-forward optimize plus the optimization-surface grid
sweep, returned as plain data for the Studio's results panels.

Lives in ``tester/`` (no Qt) so the Studio can run it on a background QThread — keeping the GUI
responsive instead of freezing under a WaitCursor — and so it is unit-testable without a QApplication.
The surface sweep is run with the SAME ``workers`` + ``strategy_source`` as the walk-forward, so it
parallelizes across processes (identical numbers to the serial path, just faster) instead of
recomputing the whole grid serially in-process.
"""

from __future__ import annotations

from dataclasses import dataclass

# Cap the in-sample grid we backtest to draw the optimization surface (combos = product of axes).
SURFACE_MAX_COMBOS = 400


@dataclass
class OptimizeJobResult:
    """Everything the Studio results panels need from one Walk-forward press — plain data, no Qt."""

    wf: object                      # WalkForwardReport (stitched OOS curve + overfit verdict)
    grid: dict
    criterion: str
    chart_bars: list                # bars for the OOS price chart ([] for a portfolio run)
    is_portfolio: bool
    surface_ranked: list | None     # ranked trials for the surface, or None when skipped/failed
    best_params: dict
    overfit_level: str
    portfolio_name: str | None = None


def _surface_eligible(grid: dict) -> bool:
    """The surface needs >=2 multi-valued axes and a grid small enough to backtest exhaustively."""
    multi = sum(1 for v in grid.values() if len(v) >= 2)
    combos = 1
    for v in grid.values():
        combos *= len(v)
    return multi >= 2 and combos <= SURFACE_MAX_COMBOS


def run_optimize_job(*, strategy_cls, grid, config, wf_kwargs, strategy_source,
                     bars=None, portfolio_bars=None, portfolio_ranges=None,
                     portfolio_name=None) -> OptimizeJobResult:
    """Run the walk-forward optimize + (when the grid qualifies) the exhaustive surface sweep.

    ``wf_kwargs`` is ``dataclasses.asdict(OptimizerConfig)`` — its keys are exactly the
    ``walk_forward`` kwargs, including ``criterion`` and ``workers``. The surface sweep reuses those
    same ``workers`` + ``strategy_source`` so it runs on the worker pool; a surface failure is
    swallowed so it never sinks the walk-forward result. Pass ``portfolio_bars`` (a {sym: bars} map)
    to optimize a DataSet via ``PortfolioStrategyTester``; otherwise pass single-symbol ``bars``.
    """
    from vike_trader_app.tester import StrategyTester

    make = strategy_cls.make
    criterion = wf_kwargs.get("criterion", "sharpe")
    workers = wf_kwargs.get("workers", 0)
    is_portfolio = portfolio_bars is not None
    if is_portfolio:
        from vike_trader_app.tester.portfolio_tester import PortfolioStrategyTester
        tester = PortfolioStrategyTester(portfolio_bars, config, ranges=portfolio_ranges)
        chart_bars: list = []   # portfolio runs have no single per-bar price chart
    else:
        tester = StrategyTester(strategy_cls(), bars, config)
        chart_bars = bars

    wf = tester.walk_forward(make, grid, **wf_kwargs, strategy_source=strategy_source)

    surface_ranked = None
    if _surface_eligible(grid):
        try:
            rep = tester.optimize(make, grid, criterion=criterion, method="grid",
                                  workers=workers, strategy_source=strategy_source)
            surface_ranked = rep.ranked
        except Exception:  # noqa: BLE001 - the surface is a nice-to-have, never block the WF result
            surface_ranked = None

    best_params = wf.windows[-1].best_params if getattr(wf, "windows", None) else {}
    verdict = getattr(wf.oos_report, "verdict", None)
    overfit_level = verdict.level if verdict else "?"
    return OptimizeJobResult(
        wf=wf, grid=grid, criterion=criterion, chart_bars=chart_bars, is_portfolio=is_portfolio,
        surface_ranked=surface_ranked, best_params=best_params, overfit_level=overfit_level,
        portfolio_name=portfolio_name,
    )
