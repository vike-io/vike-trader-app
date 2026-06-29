"""StrategyTester.optimize ranks a grid via the event-engine Backtester (config-consistent)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.tester import OptimizeReport, StrategyTester, TesterConfig, TesterReport


class _ThresholdStrat(Strategy):
    edge = True

    def on_bar(self, bar):  # noqa: ARG002
        if self.index == 0 and self.edge:
            self.buy(1.0)


def _bars():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    return [Bar(ts=i * 60_000, open=closes[i], high=closes[i] + 1, low=closes[i] - 1, close=closes[i])
            for i in range(len(closes))]


def _make(**params):
    return _ThresholdStrat.make(**params)


def test_optimize_ranks_and_returns_report():
    st = StrategyTester(_make, _bars(), TesterConfig(taker_fee=0.0))
    rep = st.optimize(_make, {"edge": [True, False]}, criterion="total_return")
    assert isinstance(rep, OptimizeReport)
    assert rep.n_trials == 2
    assert isinstance(rep.best.report, TesterReport)
    assert rep.best.params == {"edge": True}
    assert rep.ranked[0].score >= rep.ranked[-1].score
    assert 1.0 <= rep.effective_n <= 2.0


def test_optimize_effective_n_and_trial_scores_present():
    st = StrategyTester(_make, _bars(), TesterConfig())
    rep = st.optimize(_make, {"edge": [True, False]}, criterion="sharpe")
    assert len(rep.trial_scores) == 2
    assert isinstance(rep.effective_n, float)


def test_optimize_rejects_unknown_criterion():
    import pytest
    st = StrategyTester(_make, _bars(), TesterConfig())
    with pytest.raises(ValueError):
        st.optimize(_make, {"edge": [True]}, criterion="bogus")


def test_optimize_grid_is_default_method_unchanged():
    st = StrategyTester(_make, _bars(), TesterConfig(taker_fee=0.0))
    rep = st.optimize(_make, {"edge": [True, False]}, criterion="total_return", method="grid")
    assert rep.n_trials == 2  # full grid evaluated
    assert rep.best.params == {"edge": True}


def test_optimize_genetic_finds_best_and_caches():
    st = StrategyTester(_make, _bars(), TesterConfig(taker_fee=0.0))
    rep = st.optimize(_make, {"edge": [True, False]}, criterion="total_return",
                      method="genetic", seed=1, pop_size=4, generations=3)
    assert rep.best.params == {"edge": True}
    assert rep.ranked[0].score >= rep.ranked[-1].score
    # eval cache => at most the 2 distinct combos appear, never duplicated
    assert rep.n_trials <= 2
    assert len({tuple(sorted(t.params.items())) for t in rep.ranked}) == rep.n_trials


def test_optimize_random_method_runs():
    st = StrategyTester(_make, _bars(), TesterConfig(taker_fee=0.0))
    rep = st.optimize(_make, {"edge": [True, False]}, criterion="total_return",
                      method="random", seed=0, n_trials=5)
    assert rep.best.params == {"edge": True}
    assert isinstance(rep.best.report, TesterReport)


def test_optimize_bayesian_method_runs():
    import pytest
    pytest.importorskip("optuna")
    st = StrategyTester(_make, _bars(), TesterConfig(taker_fee=0.0))
    rep = st.optimize(_make, {"edge": [True, False]}, criterion="total_return",
                      method="bayesian", seed=0, n_trials=6)
    assert rep.best.params in ({"edge": True}, {"edge": False})
    assert rep.ranked[0].score >= rep.ranked[-1].score


def test_optimize_rejects_unknown_method():
    import pytest
    st = StrategyTester(_make, _bars(), TesterConfig())
    with pytest.raises(ValueError):
        st.optimize(_make, {"edge": [True]}, method="bogus")
