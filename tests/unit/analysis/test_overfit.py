"""Anti-overfitting statistics: PSR, deflated Sharpe, PBO (CSCV), verdict."""

import pytest

from vike_trader_app.analysis.overfit import (
    deflated_sharpe_ratio,
    expected_max_sharpe,
    overfit_verdict,
    pbo_cscv,
    probabilistic_sharpe_ratio,
)

# --- Probabilistic Sharpe Ratio ---


def test_psr_equals_half_when_sr_equals_benchmark():
    assert probabilistic_sharpe_ratio(0.1, n=100, sr_star=0.1) == pytest.approx(0.5)


def test_psr_above_half_when_sr_beats_benchmark():
    assert probabilistic_sharpe_ratio(0.1, n=100, sr_star=0.0) > 0.5


def test_psr_increases_with_more_observations():
    assert probabilistic_sharpe_ratio(0.1, 1000, 0.0) > probabilistic_sharpe_ratio(0.1, 100, 0.0)


# --- expected maximum Sharpe across trials ---


def test_expected_max_sharpe_zero_for_single_trial():
    assert expected_max_sharpe(var_trials=1.0, n_trials=1) == 0.0


def test_expected_max_sharpe_grows_with_trials():
    assert expected_max_sharpe(1.0, 100) > expected_max_sharpe(1.0, 10)


def test_expected_max_sharpe_scales_with_sqrt_variance():
    assert expected_max_sharpe(4.0, 50) == pytest.approx(2 * expected_max_sharpe(1.0, 50))


# --- Deflated Sharpe Ratio ---


def test_deflated_is_in_unit_interval():
    dsr = deflated_sharpe_ratio(0.3, [0.0, 0.1, 0.2], n_obs=1000)
    assert 0.0 <= dsr <= 1.0


def test_deflation_lowers_psr_vs_zero_benchmark():
    trials = [0.0, 0.1, 0.2, 0.25]
    dsr = deflated_sharpe_ratio(0.3, trials, n_obs=1000)
    raw = probabilistic_sharpe_ratio(0.3, 1000, 0.0)
    assert dsr < raw


# --- PBO via CSCV ---


def test_pbo_zero_when_one_trial_dominates():
    m = [[1.0, 0.5, 0.0] for _ in range(8)]  # trial 0 best in every observation
    assert pbo_cscv(m, n_splits=4) == 0.0


def test_pbo_positive_for_anticorrelated_performance():
    rows = []
    for g in range(4):
        val = [1.0, -1.0] if g < 2 else [-1.0, 1.0]
        rows += [val, val]  # 2 rows per group, 8 total
    assert pbo_cscv(rows, n_splits=4) > 0.0


def test_pbo_in_unit_interval():
    m = [[0.1, 0.2, -0.1, 0.05] for _ in range(12)]
    assert 0.0 <= pbo_cscv(m, n_splits=4) <= 1.0


def test_pbo_requires_even_splits():
    with pytest.raises(ValueError):
        pbo_cscv([[1.0, 0.0] for _ in range(6)], n_splits=3)


# --- Verdict ---


def test_verdict_low_risk_when_clean():
    v = overfit_verdict(pbo=0.0, deflated_sr=0.99)
    assert v.level == "Low"


def test_verdict_high_risk_when_overfit():
    v = overfit_verdict(pbo=0.7, deflated_sr=0.2)
    assert v.level == "High"
    assert v.reasons


def test_verdict_medium_in_between():
    v = overfit_verdict(pbo=0.3, deflated_sr=0.95)
    assert v.level == "Medium"
