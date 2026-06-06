"""effective_n_trials collapses correlated trials toward 1; independent trials stay ~N."""

from vike_trader_app.analysis.overfit import effective_n_trials, deflated_sharpe_with_effective_n


def test_identical_series_collapse_to_one():
    s = [0.01, -0.02, 0.03, 0.00, 0.015]
    assert effective_n_trials([s, s, s, s]) == 1.0


def test_anticorrelated_or_independent_stay_high():
    a = [0.01, -0.02, 0.03, -0.01, 0.02]
    b = [-0.01, 0.02, -0.03, 0.01, -0.02]
    assert effective_n_trials([a, b]) == 2.0


def test_single_or_empty():
    assert effective_n_trials([]) == 0.0
    assert effective_n_trials([[0.1, 0.2]]) == 1.0


def test_dsr_with_effective_n_is_a_float_in_unit_interval():
    series = [[0.01 * (i + 1), -0.005, 0.02, 0.0, 0.01] for i in range(6)]
    dsr = deflated_sharpe_with_effective_n(observed_sr=0.15,
                                           trial_sharpes=[0.15, 0.1, 0.12, 0.08, 0.11, 0.09],
                                           trial_return_series=series, n_obs=500)
    assert 0.0 <= dsr <= 1.0
