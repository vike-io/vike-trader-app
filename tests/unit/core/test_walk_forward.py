"""StrategyTester.walk_forward: per-window optimize->OOS, stitched report with an overfit verdict."""

from vike_trader_app.analysis.overfit import Verdict
from vike_trader_app.core.engine import Result
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.tester import StrategyTester, TesterConfig, TesterReport, WalkForwardReport


class _EdgeStrat(Strategy):
    edge = True

    def on_bar(self, bar):  # noqa: ARG002
        if self.edge and self.index % 4 == 0:
            self.buy(1.0)
        elif self.index % 4 == 2:
            self.close()


def _bars(n=24):
    closes = [100.0 + i for i in range(n)]
    return [Bar(ts=i * 60_000, open=closes[i], high=closes[i] + 1, low=closes[i] - 1, close=closes[i])
            for i in range(n)]


def _make(**p):
    return _EdgeStrat.make(**p)


def test_walk_forward_structure_and_verdict():
    st = StrategyTester(_make, _bars(24), TesterConfig(taker_fee=0.0))
    rep = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2, criterion="total_return")
    assert isinstance(rep, WalkForwardReport)
    assert rep.n_windows == 2 and len(rep.windows) == 2
    for w in rep.windows:
        assert isinstance(w.oos_report, TesterReport)
        assert "edge" in w.best_params
        assert len(w.train_range) == 2 and len(w.test_range) == 2
    assert isinstance(rep.oos_report, TesterReport)
    assert isinstance(rep.oos_report.verdict, Verdict)
    assert rep.oos_report.verdict.level in {"Low", "Medium", "High"}
    assert 0.0 <= rep.wf_consistency <= 1.0


def test_walk_forward_is_deterministic():
    st = StrategyTester(_make, _bars(24), TesterConfig())
    a = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2)
    b = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2)
    assert a.oos_report.equity_curve == b.oos_report.equity_curve


def test_tester_report_verdict_defaults_none():
    rep = TesterReport.from_result(Result([], [10_000.0], 10_000.0))
    assert rep.verdict is None


def test_walk_forward_rolling_mode_slides_train_window():
    st = StrategyTester(_make, _bars(24), TesterConfig(taker_fee=0.0))
    anchored = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2,
                               criterion="total_return", mode="anchored")
    rolling = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2,
                              criterion="total_return", mode="rolling")
    # anchored: every train starts at bar 0. rolling: the train window slides forward.
    assert all(w.train_range[0] == 0 for w in anchored.windows)
    assert rolling.windows[0].train_range[0] == 0
    assert rolling.windows[1].train_range[0] > 0


def test_walk_forward_efficiency_and_per_window_scores():
    st = StrategyTester(_make, _bars(24), TesterConfig(taker_fee=0.0))
    rep = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2, criterion="total_return")
    assert isinstance(rep.wf_efficiency, float)
    for w in rep.windows:
        assert isinstance(w.is_score, float)
        assert isinstance(w.oos_score, float)


def test_walk_forward_genetic_method_runs():
    st = StrategyTester(_make, _bars(24), TesterConfig(taker_fee=0.0))
    rep = st.walk_forward(_make, {"edge": [True, False]}, n_splits=2, criterion="total_return",
                          method="genetic", seed=1, pop_size=4, generations=2)
    assert rep.n_windows == 2
    for w in rep.windows:
        assert "edge" in w.best_params


def test_wf_efficiency_guards_nonpositive_is_edge():
    import pytest
    from vike_trader_app.tester.strategy_tester import wf_efficiency

    class _W:
        def __init__(self, is_score, oos_score):
            self.is_score = is_score
            self.oos_score = oos_score

    # both windows losing in-sample -> non-positive IS mean -> 0.0 (no sign-inverted "robust" ratio)
    assert wf_efficiency([_W(-1.0, -2.0), _W(-1.0, -2.0)]) == 0.0
    # near-cancelling IS mean -> 0.0, not a blown-up ratio
    assert wf_efficiency([_W(1.0, 0.5), _W(-1.0000001, 0.5)]) == 0.0
    # healthy positive IS edge -> the real ratio
    assert wf_efficiency([_W(2.0, 1.0), _W(2.0, 1.0)]) == pytest.approx(0.5)
    assert wf_efficiency([]) == 0.0
