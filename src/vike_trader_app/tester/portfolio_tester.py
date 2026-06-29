"""MultiSymbolStrategyTester — optimize / walk-forward a single-symbol Strategy across a DataSet.

The single-symbol :class:`StrategyTester` optimizes one symbol's equity curve. This mirrors it
exactly, but each trial is a *portfolio* backtest: one copy of the strategy per symbol, shared cash,
MaxOpenPositions, dynamic membership — driven by ``MultiSymbolStrategyRunner``. Every metric/verdict
(OptimizeReport / WalkForwardReport / DSR / PBO / effective-N) is the same machinery, scored on
PORTFOLIO equity instead of a single symbol's. Walk-forward windows are DATE-based (the per-symbol
series are sliced to a shared ``[ts_lo, ts_hi)`` time window), so membership ranges apply per split.
"""

from ..core.portfolio_adapter import MultiSymbolStrategyRunner
from ..core.portfolio_fastsim import CrossSectionalSignalStrategy, data_from_bars, kernel_result_to_obj
from .config import TesterConfig
from .report import TesterReport
from .strategy_tester import _CRITERIA, _OptimizeMixin, assemble_walk_forward


class MultiSymbolStrategyTester(_OptimizeMixin):
    """Optimize / walk-forward a single-symbol ``Strategy`` across a DataSet, scored on PORTFOLIO equity."""

    def __init__(self, bars_by_symbol: dict, config: TesterConfig | None = None, *,
                 max_open_positions: int = 0, ranges: dict | None = None):
        self.bars_by_symbol = bars_by_symbol
        self.config = config or TesterConfig()
        self.max_open_positions = max_open_positions
        # Optional per-symbol membership windows {symbol: [DateRange, ...]} (dynamic DataSet).
        self.ranges = ranges

    def _run_one(self, make_or_cls, bars_by_symbol: dict | None = None) -> TesterReport:
        """Run a single backtest trial, routing to the vectorized kernel when the strategy is a
        :class:`~vike_trader_app.core.portfolio_fastsim.CrossSectionalSignalStrategy`.

        ``make_or_cls`` is a zero-arg callable that returns (or IS) the strategy instance.
        ``bars_by_symbol`` defaults to ``self.bars_by_symbol``; pass a time-slice for walk-forward.
        """
        bbs = bars_by_symbol if bars_by_symbol is not None else self.bars_by_symbol
        strat = make_or_cls()
        if isinstance(strat, CrossSectionalSignalStrategy):
            # Fast path: build (T, S) matrices once, run the compiled kernel, wrap result.
            cfg = self.config
            taker_fee = cfg.taker_fee if cfg.taker_fee is not None else cfg.fee_rate
            data = data_from_bars(bbs)
            kernel_dict = strat.run(
                data,
                taker_fee=taker_fee,
                slippage=cfg.slippage,
                init_cash=cfg.cash,
                multiplier=cfg.multiplier,
            )
            result = kernel_result_to_obj(kernel_dict, data["ts"])
            return TesterReport.from_result(result, periods_per_year=cfg.periods_per_year)
        # Slow path: event MultiSymbolEngine (unchanged for all other strategy types).
        return MultiSymbolStrategyRunner(
            make_or_cls, bbs, self.config,
            max_open_positions=self.max_open_positions, ranges=self.ranges,
        ).report()

    def run(self, strategy_cls) -> TesterReport:
        """Single portfolio backtest of ``strategy_cls`` (a zero-arg callable -> Strategy)."""
        return self._run_one(strategy_cls)

    # optimize() is inherited from _OptimizeMixin (search + ranking + overfit bookkeeping); only the
    # per-trial PORTFOLIO runner differs:
    def _run_trial(self, make, params: dict) -> TesterReport:
        # mp=params default-arg capture: each lambda binds ITS OWN combo (no late-binding bug),
        # so every per-symbol strategy copy in this trial is built with this combo's params.
        return self._run_one(lambda mp=params: make(**mp))

    def walk_forward(self, make, param_grid: dict, *, n_splits: int = 4, criterion: str = "sharpe",
                     mode: str = "anchored", method: str = "grid", seed: int = 0,
                     n_trials: int | None = None, pop_size: int = 20, generations: int = 10,
                     mutation_rate: float = 0.2, sampler: str = "tpe",
                     workers: int = 1, strategy_source: str | None = None):
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

            opt = MultiSymbolStrategyTester(
                train_slice, self.config, max_open_positions=self.max_open_positions, ranges=self.ranges,
            ).optimize(make, param_grid, criterion=criterion, method=method, seed=seed, n_trials=n_trials,
                       pop_size=pop_size, generations=generations, mutation_rate=mutation_rate, sampler=sampler,
                       workers=workers, strategy_source=strategy_source)
            final_curves = [t.report.equity_curve for t in opt.ranked]

            best = opt.best
            # Route OOS through _run_one so CrossSectionalSignalStrategy uses the kernel fast path.
            oos_tester = MultiSymbolStrategyTester(
                test_slice, self.config,
                max_open_positions=self.max_open_positions, ranges=self.ranges,
            )
            oos_window = oos_tester._run_one(lambda bp=best.params: make(**bp), test_slice)
            # WalkForwardWindow.oos_report is a TesterReport (wf_consistency reads .total_return).
            windows.append(WalkForwardWindow((tr_lo, tr_hi), (te_lo, te_hi), best.params, oos_window,
                                             is_score=best.score, oos_score=getattr(oos_window, criterion)))

            start = equity
            for v in oos_window.equity_curve:
                stitched.append(start * (v / cash))
            equity = start * (oos_window.final_equity / cash)
            concat_trades.extend(oos_window.trades)

        return assemble_walk_forward(windows, final_curves, stitched, concat_trades, self.config)

    def _slice(self, lo: int, hi: int) -> dict:
        """Per-symbol bars within ``[lo, hi)``; symbols with no bars in the window are dropped."""
        out = {}
        for s, bars in self.bars_by_symbol.items():
            kept = [b for b in bars if lo <= b.ts < hi]
            if kept:
                out[s] = kept
        return out
