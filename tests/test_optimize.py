"""StrategyTester.optimize ranks a grid via the event-engine Backtester (config-consistent)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
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
