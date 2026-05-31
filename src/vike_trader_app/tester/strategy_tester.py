"""StrategyTester — the MT5-style facade: .run() single backtest, .optimize() grid sweep."""

import itertools

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

    def optimize(self, make, param_grid: dict, *, criterion: str = "sharpe") -> OptimizeReport:
        """Run every combination in ``param_grid`` through the event engine; rank by ``criterion``.

        ``make(**params) -> Strategy``. ``criterion`` is a TesterReport metric name. Each combo uses
        the SAME TesterConfig as ``run()`` (consistent costs); trial return-series feed a
        correlation-aware effective trial count for the overfit verdict.
        """
        if criterion not in _CRITERIA:
            raise ValueError(f"unknown criterion {criterion!r}; expected one of {_CRITERIA}")
        keys = list(param_grid)
        combos = [dict(zip(keys, c, strict=True)) for c in itertools.product(*(param_grid[k] for k in keys))]
        trials = []
        for params in combos:
            report = Backtester(make(**params), self.data, self.config).run()
            trials.append(OptimizeTrial(params=params, score=getattr(report, criterion), report=report))
        trials.sort(key=lambda t: t.score, reverse=True)
        return_series = [_returns(t.report.equity_curve) for t in trials]
        return OptimizeReport(
            best=trials[0], ranked=trials, trial_scores=[t.score for t in trials],
            n_trials=len(trials), effective_n=effective_n_trials(return_series), criterion=criterion,
        )


    def walk_forward(self, make, param_grid: dict, *, n_splits: int = 4, criterion: str = "sharpe"):
        """Per-window optimize-on-train -> run-best-OOS-on-test, stitched, with an overfit verdict.

        Each window uses the same TesterConfig (consistent costs). Returns a ``WalkForwardReport``
        whose stitched ``oos_report.verdict`` is an ``analysis.overfit.Verdict``.
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
        all_trial_returns: list = []
        final_curves: list = []
        for tr_s, tr_e, te_s, te_e in walk_forward_splits(len(self.data), n_splits):
            opt = StrategyTester(make, self.data[tr_s:tr_e], self.config).optimize(
                make, param_grid, criterion=criterion
            )
            for t in opt.ranked:
                all_trial_returns.append(_returns(t.report.equity_curve))
            final_curves = [t.report.equity_curve for t in opt.ranked]
            oos = Backtester(make(**opt.best.params), self.data[te_s:te_e], self.config).run()
            windows.append(WalkForwardWindow((tr_s, tr_e), (te_s, te_e), opt.best.params, oos))
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
        dsr = deflated_sharpe_with_effective_n(
            observed_sr, trial_sharpes, all_trial_returns, max(len(stitched) - 1, 2)
        )
        oos_report.verdict = overfit_verdict(_pbo_from_curves(final_curves), dsr, wf_consistency)
        return WalkForwardReport(windows=windows, oos_report=oos_report,
                                 wf_consistency=wf_consistency, n_windows=len(windows))


def _returns(equity_curve) -> list:
    """Per-step simple returns of an equity curve (for trial-correlation / effective-N)."""
    return [equity_curve[i] / equity_curve[i - 1] - 1.0
            for i in range(1, len(equity_curve)) if equity_curve[i - 1] != 0]
