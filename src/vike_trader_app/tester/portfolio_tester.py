"""PortfolioStrategyTester — optimize / walk-forward a single-symbol Strategy across a DataSet.

The single-symbol :class:`StrategyTester` optimizes one symbol's equity curve. This mirrors it
exactly, but each trial is a *portfolio* backtest: one copy of the strategy per symbol, shared cash,
MaxOpenPositions, dynamic membership — driven by ``MultiSymbolStrategyRunner``. Every metric/verdict
(OptimizeReport / WalkForwardReport / DSR / PBO / effective-N) is the same machinery, scored on
PORTFOLIO equity instead of a single symbol's. Walk-forward windows are DATE-based (the per-symbol
series are sliced to a shared ``[ts_lo, ts_hi)`` time window), so membership ranges apply per split.
"""

from ..analysis import samplers
from ..analysis.metrics import returns
from ..analysis.overfit import effective_n_trials
from ..core.portfolio_adapter import MultiSymbolStrategyRunner
from .config import TesterConfig
from .optimize import OptimizeReport, OptimizeTrial
from .report import TesterReport
from .strategy_tester import _CRITERIA, assemble_walk_forward


class PortfolioStrategyTester:
    """Optimize / walk-forward a single-symbol ``Strategy`` across a DataSet, scored on PORTFOLIO equity."""

    def __init__(self, bars_by_symbol: dict, config: TesterConfig | None = None, *,
                 max_open_positions: int = 0, ranges: dict | None = None):
        self.bars_by_symbol = bars_by_symbol
        self.config = config or TesterConfig()
        self.max_open_positions = max_open_positions
        # Optional per-symbol membership windows {symbol: [DateRange, ...]} (dynamic DataSet).
        self.ranges = ranges

    def run(self, strategy_cls) -> TesterReport:
        """Single portfolio backtest of ``strategy_cls`` (a zero-arg callable -> Strategy)."""
        return MultiSymbolStrategyRunner(
            strategy_cls, self.bars_by_symbol, self.config,
            max_open_positions=self.max_open_positions, ranges=self.ranges,
        ).report()

    def optimize(self, make, param_grid: dict, *, criterion: str = "sharpe", method: str = "grid",
                 seed: int = 0, n_trials: int | None = None, pop_size: int = 20,
                 generations: int = 10, mutation_rate: float = 0.2, sampler: str = "tpe") -> OptimizeReport:
        """Search ``param_grid`` with ``method`` as PORTFOLIO backtests; rank by ``criterion``.

        ``make(**params) -> Strategy``. ``criterion`` is a TesterReport metric. ``method`` is grid /
        random / genetic / bayesian (see ``StrategyTester.optimize``). Each combo uses the SAME
        TesterConfig / membership ranges as ``run()`` (consistent costs); an evaluation cache runs
        each distinct combo once. Trial portfolio-equity return-series feed the correlation-aware
        effective trial count for the overfit verdict.
        """
        if criterion not in _CRITERIA:
            raise ValueError(f"unknown criterion {criterion!r}; expected one of {_CRITERIA}")
        reports: dict[tuple, TesterReport] = {}

        def objective(params: dict) -> float:
            key = tuple(sorted(params.items()))
            rep = reports.get(key)
            if rep is None:
                # mp=params default-arg capture: each lambda binds ITS OWN combo (no late-binding bug),
                # so every per-symbol strategy copy in this trial is built with this combo's params.
                rep = MultiSymbolStrategyRunner(
                    lambda mp=params: make(**mp), self.bars_by_symbol, self.config,
                    max_open_positions=self.max_open_positions, ranges=self.ranges,
                ).report()
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

    def walk_forward(self, make, param_grid: dict, *, n_splits: int = 4, criterion: str = "sharpe",
                     mode: str = "anchored", method: str = "grid", seed: int = 0,
                     n_trials: int | None = None, pop_size: int = 20, generations: int = 10,
                     mutation_rate: float = 0.2, sampler: str = "tpe"):
        """Per-window optimize-on-train -> run-best-OOS-on-test over the portfolio, stitched + verdict.

        Windows are DATE-based over the aligned union timeline: each index split maps to a shared
        ``[ts_lo, ts_hi)`` time window, and every symbol's bars are sliced to it (symbols with no bars
        in the window are dropped). Membership ``ranges`` thread through to every runner so the
        survivorship windows still apply within each split. Returns a ``WalkForwardReport`` whose
        stitched ``oos_report.verdict`` is an ``analysis.overfit.Verdict``.
        """
        from ..analysis.validation import walk_forward_splits
        from .walkforward import WalkForwardWindow

        ts_all = sorted({b.ts for bars in self.bars_by_symbol.values() for b in bars})

        cash = self.config.cash
        equity = cash
        stitched: list = []
        concat_trades: list = []
        windows: list = []
        final_curves: list = []
        # Half-open time bound for index ``idx`` into ``ts_all``: the timestamp AT ``idx`` (the start
        # of the next bar), or one past the last timestamp when ``idx`` runs off the end. Using the
        # next bar's ts (not last_ts+1) keeps consecutive windows exactly contiguous: hi0 == lo1.
        def _hi(idx: int) -> int:
            return ts_all[idx] if idx < len(ts_all) else ts_all[-1] + 1

        for tr_s, tr_e, te_s, te_e in walk_forward_splits(len(ts_all), n_splits, mode=mode):
            tr_lo, tr_hi = ts_all[tr_s], _hi(tr_e)   # [tr_lo, tr_hi) half-open in time
            te_lo, te_hi = ts_all[te_s], _hi(te_e)
            train_slice = self._slice(tr_lo, tr_hi)
            test_slice = self._slice(te_lo, te_hi)

            opt = PortfolioStrategyTester(
                train_slice, self.config, max_open_positions=self.max_open_positions, ranges=self.ranges,
            ).optimize(make, param_grid, criterion=criterion, method=method, seed=seed, n_trials=n_trials,
                       pop_size=pop_size, generations=generations, mutation_rate=mutation_rate, sampler=sampler)
            final_curves = [t.report.equity_curve for t in opt.ranked]

            best = opt.best
            oos = MultiSymbolStrategyRunner(
                lambda bp=best.params: make(**bp), test_slice, self.config,
                max_open_positions=self.max_open_positions, ranges=self.ranges,
            ).run()
            # WalkForwardWindow.oos_report is a TesterReport (wf_consistency reads .total_return).
            oos_window = TesterReport.from_result(oos, periods_per_year=self.config.periods_per_year)
            windows.append(WalkForwardWindow((tr_lo, tr_hi), (te_lo, te_hi), best.params, oos_window,
                                             is_score=best.score, oos_score=getattr(oos_window, criterion)))

            start = equity
            for v in oos.equity_curve:
                stitched.append(start * (v / cash))
            equity = start * (oos.final_equity / cash)
            concat_trades.extend(oos.trades)

        return assemble_walk_forward(windows, final_curves, stitched, concat_trades, self.config)

    def _slice(self, lo: int, hi: int) -> dict:
        """Per-symbol bars within ``[lo, hi)``; symbols with no bars in the window are dropped."""
        out = {}
        for s, bars in self.bars_by_symbol.items():
            kept = [b for b in bars if lo <= b.ts < hi]
            if kept:
                out[s] = kept
        return out
