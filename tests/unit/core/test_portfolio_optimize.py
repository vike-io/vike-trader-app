"""MultiSymbolStrategyTester: optimize + walk-forward scored on PORTFOLIO equity over a DataSet."""

from vike_trader_app.analysis.overfit import Verdict
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.data.datasets import DateRange
from vike_trader_app.tester import OptimizeReport, TesterConfig, TesterReport, WalkForwardReport
from vike_trader_app.tester.portfolio_tester import MultiSymbolStrategyTester


class _Sma(Strategy):
    """Tiny SMA crossover so different (fast, slow) give different portfolio scores."""

    fast = 2
    slow = 4
    WARMUP = 4

    def __init__(self):
        super().__init__()
        self._c = []

    def on_bar(self, bar):
        self._c.append(bar.close)
        if len(self._c) < self.slow:
            return
        f = sum(self._c[-self.fast:]) / self.fast
        s = sum(self._c[-self.slow:]) / self.slow
        if f > s and self.position.size == 0:
            self.buy(1.0)
        elif f < s and self.position.size != 0:
            self.close()


def _make(**p):
    inst = _Sma()
    for k, v in p.items():
        setattr(inst, k, v)
    return inst


def _wave(n, base, amp, period=6):
    """A choppy series (so the SMA crossover both enters and exits) on a 1-minute grid."""
    import math

    out = []
    for i in range(n):
        c = base + amp * math.sin(2 * math.pi * i / period) + 0.05 * i
        out.append(Bar(ts=i * 60_000, open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1.0))
    return out


def _dataset(n=40):
    return {"AAA": _wave(n, 100.0, 4.0), "BBB": _wave(n, 50.0, 3.0, period=8)}


_GRID = {"fast": [2, 3], "slow": [4, 6]}


# --- optimize ------------------------------------------------------------------------------------


def test_optimize_returns_ranked_report_over_portfolio_equity():
    pt = MultiSymbolStrategyTester(_dataset(40), TesterConfig(taker_fee=0.0))
    rep = pt.optimize(_make, _GRID, criterion="total_return")

    assert isinstance(rep, OptimizeReport)
    assert rep.n_trials == 4  # 2 x 2 grid
    assert rep.criterion == "total_return"
    # ranked descending by score; best == ranked[0]
    assert rep.ranked[0] is rep.best
    scores = [t.score for t in rep.ranked]
    assert scores == sorted(scores, reverse=True)
    # best is genuinely the top-scoring combo
    assert rep.best.score == max(t.score for t in rep.ranked)
    assert isinstance(rep.effective_n, float)
    assert 1.0 <= rep.effective_n <= 4.0


def test_optimize_scores_on_portfolio_equity_not_single_symbol():
    bars = _dataset(40)
    pt = MultiSymbolStrategyTester(bars, TesterConfig(taker_fee=0.0))
    rep = pt.optimize(_make, _GRID, criterion="sharpe")
    best = rep.best.report

    # Portfolio equity curve spans every aligned bar of the union timeline.
    aligned_len = len(sorted({b.ts for series in bars.values() for b in series}))
    assert len(best.equity_curve) == aligned_len
    assert isinstance(best, TesterReport)
    # The portfolio report carries per-symbol PnL for BOTH symbols (a single-symbol run would not).
    assert best.per_symbol_pnl is not None
    assert set(best.per_symbol_pnl) == {"AAA", "BBB"}


def test_optimize_rejects_unknown_criterion():
    import pytest

    pt = MultiSymbolStrategyTester(_dataset(20), TesterConfig())
    with pytest.raises(ValueError):
        pt.optimize(_make, _GRID, criterion="bogus")


def test_optimize_closure_capture_distinct_params_per_combo():
    # Each trial's report must reflect ITS OWN params (no late-binding closure bug). Asserting the
    # ranked trials don't all collapse to one identical score proves the params actually varied.
    pt = MultiSymbolStrategyTester(_dataset(40), TesterConfig(taker_fee=0.0))
    rep = pt.optimize(_make, _GRID, criterion="total_return")
    assert len({tuple(sorted(t.params.items())) for t in rep.ranked}) == 4


# --- walk_forward --------------------------------------------------------------------------------


def test_walk_forward_structure_and_verdict():
    pt = MultiSymbolStrategyTester(_dataset(48), TesterConfig(taker_fee=0.0))
    rep = pt.walk_forward(_make, _GRID, n_splits=3, criterion="total_return")

    assert isinstance(rep, WalkForwardReport)
    assert rep.n_windows == 3 and len(rep.windows) == 3
    assert isinstance(rep.oos_report, TesterReport)
    assert isinstance(rep.oos_report.verdict, Verdict)
    assert rep.oos_report.verdict.level in {"Low", "Medium", "High"}
    assert 0.0 <= rep.wf_consistency <= 1.0
    for w in rep.windows:
        assert "fast" in w.best_params and "slow" in w.best_params
        assert len(w.train_range) == 2 and len(w.test_range) == 2


def test_walk_forward_windows_are_time_tuples_non_overlapping():
    pt = MultiSymbolStrategyTester(_dataset(48), TesterConfig(taker_fee=0.0))
    rep = pt.walk_forward(_make, _GRID, n_splits=3, criterion="total_return")
    tests = [w.test_range for w in rep.windows]
    # train/test ranges are (ts_lo, ts_hi) time tuples
    for lo, hi in tests:
        assert lo < hi
    # OOS test windows are contiguous and non-overlapping in time
    for (lo0, hi0), (lo1, hi1) in zip(tests, tests[1:]):
        assert hi0 == lo1, "consecutive OOS windows must not overlap or gap"


def test_walk_forward_is_deterministic():
    pt = MultiSymbolStrategyTester(_dataset(48), TesterConfig())
    a = pt.walk_forward(_make, _GRID, n_splits=3)
    b = pt.walk_forward(_make, _GRID, n_splits=3)
    assert a.oos_report.equity_curve == b.oos_report.equity_curve


# --- dynamic membership (survivorship-free) ------------------------------------------------------


def test_optimize_respects_membership_ranges():
    bars = _dataset(40)
    # BBB is never a member -> it must never trade in any trial.
    ranges = {"BBB": [DateRange(10**15, 10**15 + 1)]}  # a window entirely outside the data
    pt = MultiSymbolStrategyTester(bars, TesterConfig(taker_fee=0.0), ranges=ranges)
    rep = pt.optimize(_make, _GRID, criterion="total_return")
    for t in rep.ranked:
        assert t.report.per_symbol_pnl.get("BBB", 0.0) == 0.0
        assert all(tr.symbol != "BBB" for tr in t.report.trades)


def test_walk_forward_respects_membership_ranges():
    bars = _dataset(48)
    ranges = {"BBB": [DateRange(10**15, 10**15 + 1)]}  # BBB inactive everywhere
    pt = MultiSymbolStrategyTester(bars, TesterConfig(taker_fee=0.0), ranges=ranges)
    rep = pt.walk_forward(_make, _GRID, n_splits=3, criterion="total_return")
    # runs end-to-end and BBB never trades out-of-sample
    assert rep.n_windows == 3
    assert all(tr.symbol != "BBB" for tr in rep.oos_report.trades)


# --- samplers + rolling WF over the portfolio ----------------------------------------------------


def test_portfolio_optimize_genetic_method():
    pt = MultiSymbolStrategyTester(_dataset(40), TesterConfig(taker_fee=0.0))
    rep = pt.optimize(_make, _GRID, criterion="total_return", method="genetic",
                      seed=1, pop_size=4, generations=3)
    assert isinstance(rep, OptimizeReport)
    # every returned trial is a distinct combo from the 2x2 grid (eval cache, no dupes)
    assert len({tuple(sorted(t.params.items())) for t in rep.ranked}) == rep.n_trials
    assert rep.ranked[0] is rep.best


def test_portfolio_walk_forward_rolling_and_efficiency():
    pt = MultiSymbolStrategyTester(_dataset(48), TesterConfig(taker_fee=0.0))
    rep = pt.walk_forward(_make, _GRID, n_splits=3, criterion="total_return", mode="rolling")
    assert rep.n_windows == 3
    assert isinstance(rep.wf_efficiency, float)
    # rolling time windows still contiguous + non-overlapping
    tests = [w.test_range for w in rep.windows]
    for (lo0, hi0), (lo1, hi1) in zip(tests, tests[1:]):
        assert hi0 == lo1
