"""Bootstrap confidence intervals + rule-significance p-value tests."""

import pytest

from vike_trader_app.analysis.significance import bootstrap_ci, rule_significance


def test_bootstrap_ci_brackets_the_point_estimate():
    values = [0.01] * 200  # constant -> CI collapses on the value
    low, point, high = bootstrap_ci(values, n_boot=500, seed=1)
    assert point == pytest.approx(0.01)
    assert low == pytest.approx(0.01) and high == pytest.approx(0.01)


def test_bootstrap_ci_is_ordered_and_contains_point():
    values = [0.05, -0.02, 0.03, 0.01, -0.01, 0.04, 0.02, -0.03, 0.06, 0.00]
    low, point, high = bootstrap_ci(values, n_boot=1000, seed=7)
    assert low <= point <= high


def test_rule_significance_small_p_for_strong_edge():
    returns = [0.02] * 50  # consistent positive edge
    p = rule_significance(returns, n_perm=1000, seed=3)
    assert p < 0.05


def test_rule_significance_large_p_for_no_edge():
    # symmetric around zero -> no edge -> p should be far from significant
    returns = [0.02, -0.02] * 50
    p = rule_significance(returns, n_perm=1000, seed=3)
    assert p > 0.2
