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


def _returns(equity_curve) -> list:
    """Per-step simple returns of an equity curve (for trial-correlation / effective-N)."""
    return [equity_curve[i] / equity_curve[i - 1] - 1.0
            for i in range(1, len(equity_curve)) if equity_curve[i - 1] != 0]
