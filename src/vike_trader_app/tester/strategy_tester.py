"""StrategyTester — the MT5-style facade: .run() single backtest, .optimize() grid/genetic/Bayesian sweep."""

from ..analysis import samplers
from ..analysis.overfit import effective_n_trials
from .backtester import Backtester
from .config import TesterConfig
from .optimize import OptimizeReport, OptimizeTrial
from .report import TesterReport

_CRITERIA = ("sharpe", "sortino", "calmar", "omega", "total_return", "profit_factor", "recovery_factor")


class StrategyTester:
    """Front door over the tester layer. ``data`` is the bar list for runs."""

    def __init__(self, strategy, data, config: TesterConfig | None = None):
        self.strategy = strategy
        self.data = data
        self.config = config or TesterConfig()

    def run(self) -> TesterReport:
        """Single historical backtest -> standardized report."""
        return Backtester(self.strategy, self.data, self.config).run()

    def optimize(self, make, param_grid: dict, *, criterion: str = "sharpe", method: str = "grid",
                 seed: int = 0, n_trials: int | None = None, pop_size: int = 20,
                 generations: int = 10, mutation_rate: float = 0.2, sampler: str = "tpe") -> OptimizeReport:
        """Search ``param_grid`` with ``method``, scoring each combo through the event engine; rank by ``criterion``.

        ``make(**params) -> Strategy``. ``criterion`` is a TesterReport metric name. ``method`` is
        ``grid`` (exhaustive), ``random``, ``genetic`` (no-dep GA), or ``bayesian`` (optuna: TPE /
        GP / CMA-ES via ``sampler``). Every combo uses the SAME TesterConfig as ``run()`` (consistent
        costs); an evaluation cache backtests each distinct combo once. Trial return-series feed a
        correlation-aware effective trial count for the overfit verdict.
        """
        if criterion not in _CRITERIA:
            raise ValueError(f"unknown criterion {criterion!r}; expected one of {_CRITERIA}")
        reports: dict[tuple, TesterReport] = {}

        def objective(params: dict) -> float:
            key = tuple(sorted(params.items()))
            rep = reports.get(key)
            if rep is None:
                rep = Backtester(make(**params), self.data, self.config).run()
                reports[key] = rep
            return getattr(rep, criterion)

        sampled = samplers.optimize(
            param_grid, objective, method=method, seed=seed, n_trials=n_trials,
            pop_size=pop_size, generations=generations, mutation_rate=mutation_rate, sampler=sampler,
        )
        trials = [OptimizeTrial(params=s.params, score=s.score, report=reports[tuple(sorted(s.params.items()))])
                  for s in sampled]
        return_series = [_returns(t.report.equity_curve) for t in trials]
        return OptimizeReport(
            best=trials[0], ranked=trials, trial_scores=[t.score for t in trials],
            n_trials=len(trials), effective_n=effective_n_trials(return_series), criterion=criterion,
        )


    def walk_forward(self, make, param_grid: dict, *, n_splits: int = 4, criterion: str = "sharpe",
                     mode: str = "anchored", method: str = "grid", seed: int = 0,
                     n_trials: int | None = None, pop_size: int = 20, generations: int = 10,
                     mutation_rate: float = 0.2, sampler: str = "tpe"):
        """Per-window optimize-on-train -> run-best-OOS-on-test, stitched, with an overfit verdict.

        ``mode`` selects the train window: ``anchored`` (expanding from bar 0) or ``rolling``
        (fixed-width sliding). ``method`` / sampler kwargs pick the per-window optimizer (grid /
        random / genetic / bayesian). Each window uses the same TesterConfig (consistent costs).
        Returns a ``WalkForwardReport`` (per-window IS vs OOS scores + ``wf_efficiency``) whose
        stitched ``oos_report.verdict`` is an ``analysis.overfit.Verdict``.
        """
        from ..analysis import metrics as m
        from ..analysis.overfit import deflated_sharpe_with_effective_n, overfit_verdict
        from ..analysis.validation import walk_forward_splits
        from ..core.engine import Result
        from .walkforward import WalkForwardReport, WalkForwardWindow, _pbo_from_curves

        cash = self.config.cash
        equity = cash
        stitched: list = []
        concat_trades: list = []
        windows: list = []
        final_curves: list = []
        for tr_s, tr_e, te_s, te_e in walk_forward_splits(len(self.data), n_splits, mode=mode):
            opt = StrategyTester(make, self.data[tr_s:tr_e], self.config).optimize(
                make, param_grid, criterion=criterion, method=method, seed=seed, n_trials=n_trials,
                pop_size=pop_size, generations=generations, mutation_rate=mutation_rate, sampler=sampler,
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

        oos_report = TesterReport.from_result(
            Result(concat_trades, stitched or [cash], stitched[-1] if stitched else cash),
            periods_per_year=self.config.periods_per_year,
        )
        wf_consistency = (
            sum(1 for w in windows if w.oos_report.total_return > 0) / len(windows) if windows else 0.0
        )
        observed_sr = m.sharpe(stitched, 1) if len(stitched) > 1 else 0.0
        trial_sharpes = [m.sharpe(c, 1) for c in final_curves] or [observed_sr]
        # Verdict is scoped to the final (largest-train) window's trials for coherent DSR/PBO/effective-N.
        final_returns = [_returns(c) for c in final_curves]
        dsr = deflated_sharpe_with_effective_n(
            observed_sr, trial_sharpes, final_returns, max(len(stitched) - 1, 2)
        )
        oos_report.verdict = overfit_verdict(_pbo_from_curves(final_curves), dsr, wf_consistency)
        return WalkForwardReport(windows=windows, oos_report=oos_report,
                                 wf_consistency=wf_consistency, n_windows=len(windows),
                                 wf_efficiency=wf_efficiency(windows))


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


def _returns(equity_curve) -> list:
    """Per-step simple returns of an equity curve (for trial-correlation / effective-N)."""
    return [equity_curve[i] / equity_curve[i - 1] - 1.0
            for i in range(1, len(equity_curve)) if equity_curve[i - 1] != 0]
