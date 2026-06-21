"""StrategyTester — the MT5-style facade: .run() single backtest, .optimize() grid/genetic/Bayesian sweep."""

import logging

from ..analysis import samplers
from ..analysis.metrics import returns
from ..analysis.overfit import effective_n_trials
from .backtester import Backtester
from .config import TesterConfig
from .optimize import OptimizeReport, OptimizeTrial
from .report import TesterReport

_CRITERIA = ("sharpe", "sortino", "calmar", "omega", "total_return", "profit_factor", "recovery_factor")

log = logging.getLogger(__name__)


class _OptimizeMixin:
    """Template-method ``optimize()`` shared by the single-symbol and portfolio testers. The ONLY
    per-tester difference is how one param combo is scored (the inner runner), so a subclass supplies
    ``_run_trial(make, params) -> TesterReport`` and inherits the search + ranking + overfit
    bookkeeping. (``self.config`` is set by the subclass ``__init__``.)"""

    def _run_trial(self, make, params: dict) -> TesterReport:
        raise NotImplementedError

    def optimize(self, make, param_grid: dict, *, criterion: str = "sharpe", method: str = "grid",
                 seed: int = 0, n_trials: int | None = None, pop_size: int = 20,
                 generations: int = 10, mutation_rate: float = 0.2, sampler: str = "tpe",
                 workers: int = 1, strategy_source: str | None = None,
                 _grid_pool=None) -> OptimizeReport:
        """Search ``param_grid`` with ``method``, scoring each combo through the engine; rank by
        ``criterion`` (a TesterReport metric). ``make(**params) -> Strategy``. ``method`` = ``grid``
        (exhaustive) / ``random`` / ``genetic`` (no-dep GA) / ``bayesian`` (optuna TPE/GP/CMA-ES via
        ``sampler``). Every combo uses the tester's SAME TesterConfig, cached so each distinct combo
        runs once; trial return-series feed a correlation-aware effective trial count for the verdict.

        ``workers`` > 1 with ``strategy_source`` (the strategy's source text) runs the exhaustive
        ``grid`` across that many worker processes — the combos are independent, so the cache is
        pre-filled in parallel and the numbers are identical to the serial path. Parallelism is
        best-effort: any pool failure falls back to the in-process loop, never failing the run.
        Only ``grid`` parallelizes (random/genetic/bayesian are sequential by construction)."""
        if criterion not in _CRITERIA:
            raise ValueError(f"unknown criterion {criterion!r}; expected one of {_CRITERIA}")
        reports: dict[tuple, TesterReport] = {}

        prefill = getattr(self, "_parallel_grid_prefill", None)
        if method == "grid" and strategy_source and prefill is not None:
            prefill(reports, param_grid, workers, strategy_source, _grid_pool)

        def objective(params: dict) -> float:
            key = tuple(sorted(params.items()))
            rep = reports.get(key)
            if rep is None:
                rep = self._run_trial(make, params)
                reports[key] = rep
            return getattr(rep, criterion)

        sampled = samplers.optimize(
            param_grid, objective, method=method, seed=seed, n_trials=n_trials,
            pop_size=pop_size, generations=generations, mutation_rate=mutation_rate, sampler=sampler,
        )
        trials = [OptimizeTrial(params=s.params, score=s.score, report=reports[tuple(sorted(s.params.items()))])
                  for s in sampled]
        return_series = [returns(t.report.equity_curve) for t in trials]
        return OptimizeReport(
            best=trials[0], ranked=trials, trial_scores=[t.score for t in trials],
            n_trials=len(trials), effective_n=effective_n_trials(return_series), criterion=criterion,
        )


class StrategyTester(_OptimizeMixin):
    """Front door over the tester layer. ``data`` is the bar list for runs."""

    def __init__(self, strategy, data, config: TesterConfig | None = None):
        self.strategy = strategy
        self.data = data
        self.config = config or TesterConfig()

    def run(self) -> TesterReport:
        """Single historical backtest -> standardized report."""
        return Backtester(self.strategy, self.data, self.config).run()

    def _run_trial(self, make, params: dict) -> TesterReport:
        return Backtester(make(**params), self.data, self.config).run()

    def _parallel_grid_prefill(self, reports: dict, param_grid: dict, workers: int,
                               strategy_source: str, grid_pool=None) -> None:
        """Seed ``reports`` with every grid combo's TesterReport, computed across worker processes.

        With ``grid_pool`` (a reused :class:`~.parallel.GridPool`, as walk-forward passes) the
        shared pool runs this window's combos; otherwise a fresh one-shot pool is spawned. Best-
        effort either way: a no-op when only one worker resolves, and any pool error keeps whatever
        partial results already arrived (the serial objective recomputes the missing keys) — so
        parallelism can speed optimize up but never make it FAIL."""
        from .parallel import parallel_grid_reports, resolve_workers

        if resolve_workers(workers) <= 1:
            return
        try:
            it = (grid_pool.run(param_grid, self.data, self.config) if grid_pool is not None
                  else parallel_grid_reports(strategy_source, param_grid, self.data, self.config, workers))
            for params, rep in it:
                reports[tuple(sorted(params.items()))] = rep
        except Exception:  # noqa: BLE001 - parallelism is an optimization, never a failure mode
            log.warning("parallel grid prefill failed; falling back to serial", exc_info=True)

    def walk_forward(self, make, param_grid: dict, *, n_splits: int = 4, criterion: str = "sharpe",
                     mode: str = "anchored", method: str = "grid", seed: int = 0,
                     n_trials: int | None = None, pop_size: int = 20, generations: int = 10,
                     mutation_rate: float = 0.2, sampler: str = "tpe",
                     workers: int = 1, strategy_source: str | None = None):
        """Per-window optimize-on-train -> run-best-OOS-on-test, stitched, with an overfit verdict.

        ``mode`` selects the train window: ``anchored`` (expanding from bar 0) or ``rolling``
        (fixed-width sliding). ``method`` / sampler kwargs pick the per-window optimizer (grid /
        random / genetic / bayesian). Each window uses the same TesterConfig (consistent costs).
        Returns a ``WalkForwardReport`` (per-window IS vs OOS scores + ``wf_efficiency``) whose
        stitched ``oos_report.verdict`` is an ``analysis.overfit.Verdict``.
        """
        from contextlib import nullcontext

        from ..analysis.validation import walk_forward_splits
        from .parallel import GridPool, resolve_workers
        from .walkforward import WalkForwardWindow

        cash = self.config.cash
        equity = cash
        stitched: list = []
        concat_trades: list = []
        windows: list = []
        final_curves: list = []
        # ONE worker pool reused across every window's grid: pay the Windows-spawn / GUI-import cost
        # ONCE for the whole walk-forward, not per window. nullcontext (-> pool=None) whenever
        # parallelism doesn't apply, so the serial path is byte-for-byte unchanged.
        use_pool = method == "grid" and bool(strategy_source) and resolve_workers(workers) > 1
        with (GridPool(strategy_source, workers) if use_pool else nullcontext()) as pool:
            for tr_s, tr_e, te_s, te_e in walk_forward_splits(len(self.data), n_splits, mode=mode):
                opt = StrategyTester(make, self.data[tr_s:tr_e], self.config).optimize(
                    make, param_grid, criterion=criterion, method=method, seed=seed, n_trials=n_trials,
                    pop_size=pop_size, generations=generations, mutation_rate=mutation_rate, sampler=sampler,
                    workers=workers, strategy_source=strategy_source, _grid_pool=pool,
                )
                final_curves = [t.report.equity_curve for t in opt.ranked]
                oos = Backtester(make(**opt.best.params), self.data[te_s:te_e], self.config).run()
                windows.append(WalkForwardWindow((tr_s, tr_e), (te_s, te_e), opt.best.params, oos,
                                                 is_score=opt.best.score, oos_score=getattr(oos, criterion)))
                start = equity
                for v in oos.equity_curve:
                    stitched.append(start * (v / cash))
                equity = start * (oos.final_equity / cash)
                concat_trades.extend(oos.trades)

        return assemble_walk_forward(windows, final_curves, stitched, concat_trades, self.config)


def wf_efficiency(windows) -> float:
    """mean(OOS criterion) / mean(IS criterion) across windows — the walk-forward efficiency ratio.

    Only meaningful when the in-sample edge is positive, so we return 0.0 when the mean IS score is
    non-positive (a losing/degenerate system): a negative denominator would otherwise sign-invert
    (two losing windows reading as a "robust" positive ratio) or blow up near zero. The UI renders
    the 0.0 sentinel as "—".
    """
    if not windows:
        return 0.0
    mean_is = sum(w.is_score for w in windows) / len(windows)
    mean_oos = sum(w.oos_score for w in windows) / len(windows)
    return mean_oos / mean_is if mean_is > 1e-9 else 0.0


def assemble_walk_forward(windows, final_curves, stitched, concat_trades, config):
    """Build the final WalkForwardReport from the per-window results: stitch the OOS curves into one
    report, then the consistency -> observed-Sharpe -> DSR/PBO -> verdict overfit assessment. The
    window LOOP differs (single-symbol vs portfolio), but this tail must stay IDENTICAL across both,
    so it lives here once. Verdict is scoped to the final (largest-train) window's trials for
    coherent DSR/PBO/effective-N."""
    from ..analysis import metrics as m
    from ..analysis.metrics import returns
    from ..analysis.overfit import deflated_sharpe_with_effective_n, overfit_verdict
    from ..core.engine import Result
    from .report import TesterReport
    from .walkforward import WalkForwardReport, _pbo_from_curves

    cash = config.cash
    oos_report = TesterReport.from_result(
        Result(concat_trades, stitched or [cash], stitched[-1] if stitched else cash),
        periods_per_year=config.periods_per_year,
    )
    wf_consistency = (
        sum(1 for w in windows if w.oos_report.total_return > 0) / len(windows) if windows else 0.0
    )
    observed_sr = m.sharpe(stitched, 1) if len(stitched) > 1 else 0.0
    trial_sharpes = [m.sharpe(c, 1) for c in final_curves] or [observed_sr]
    final_returns = [returns(c) for c in final_curves]
    dsr = deflated_sharpe_with_effective_n(
        observed_sr, trial_sharpes, final_returns, max(len(stitched) - 1, 2)
    )
    oos_report.verdict = overfit_verdict(_pbo_from_curves(final_curves), dsr, wf_consistency)
    return WalkForwardReport(windows=windows, oos_report=oos_report,
                             wf_consistency=wf_consistency, n_windows=len(windows),
                             wf_efficiency=wf_efficiency(windows))


