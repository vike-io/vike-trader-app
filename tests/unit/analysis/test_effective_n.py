"""effective_n_trials collapses correlated trials toward 1; independent trials stay ~N."""

import random

from vike_trader_app.analysis.overfit import _pearson, effective_n_trials, deflated_sharpe_with_effective_n


def _ref_effective_n(series):
    """Reference: the original O(N^2) pure-Python pairwise formula the numpy path replaced."""
    n = len(series)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    corrs = [_pearson(series[i], series[j]) for i in range(n) for j in range(i + 1, n)]
    avg = sum(corrs) / len(corrs) if corrs else 0.0
    r = max(avg, 0.0)
    return min(max(n / (1.0 + (n - 1) * r), 1.0), float(n))


def test_vectorized_matches_pure_python_reference():
    """The numpy fast path must equal the pure-Python pairwise reference (random + a zero-var row)."""
    rng = random.Random(7)
    for _ in range(20):
        n = rng.randint(2, 12)
        m = rng.randint(2, 40)
        series = [[rng.gauss(0, 1) for _ in range(m)] for _ in range(n)]
        if rng.random() < 0.3:                       # sometimes inject a flat (zero-variance) trial
            series[rng.randrange(n)] = [0.5] * m
        assert abs(effective_n_trials(series) - _ref_effective_n(series)) < 1e-9


def test_ragged_series_use_exact_pairwise_path():
    """Unequal lengths fall back to the exact pairwise path (per-pair min-length truncation)."""
    series = [[0.01, -0.02, 0.03, 0.0], [0.02, -0.01, 0.02], [-0.01, 0.02, -0.03, 0.01, 0.0]]
    assert abs(effective_n_trials(series) - _ref_effective_n(series)) < 1e-12


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
