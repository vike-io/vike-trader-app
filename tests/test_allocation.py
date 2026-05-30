"""Composable allocation layer: Select -> Weigh (bt-style) for portfolio rebalancing."""

import pytest

from vike_trader_app.analysis.allocation import (
    select_top_n,
    weigh_equally,
    weigh_inverse_vol,
    weigh_min_variance,
    weigh_risk_parity,
)


def test_select_top_n_picks_highest_scores():
    scores = {"A": 0.3, "B": 0.9, "C": 0.1, "D": 0.5}
    assert select_top_n(scores, 2) == ["B", "D"]


def test_weigh_equally_sums_to_one():
    w = weigh_equally(["A", "B", "C", "D"])
    assert all(v == pytest.approx(0.25) for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)


def test_weigh_inverse_vol_favours_low_vol():
    w = weigh_inverse_vol({"A": 0.2, "B": 0.1})  # 1/vol = 5, 10 -> 1/3, 2/3
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["A"] == pytest.approx(1 / 3)
    assert w["B"] == pytest.approx(2 / 3)
    assert w["B"] > w["A"]


def test_weigh_min_variance_diagonal_is_inverse_variance():
    # uncorrelated, variances 0.04 and 0.01 -> weights proportional to 1/variance = 25:100
    cov = [[0.04, 0.0], [0.0, 0.01]]
    w = weigh_min_variance(["A", "B"], cov)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["A"] == pytest.approx(0.2)
    assert w["B"] == pytest.approx(0.8)


def test_weigh_risk_parity_equal_for_uncorrelated_equal_vol():
    cov = [[0.04, 0.0], [0.0, 0.04]]  # equal vol, uncorrelated -> equal weights
    w = weigh_risk_parity(["A", "B"], cov)
    assert w["A"] == pytest.approx(0.5, abs=1e-3)
    assert w["B"] == pytest.approx(0.5, abs=1e-3)


def test_weigh_risk_parity_matches_inverse_vol_when_uncorrelated():
    # uncorrelated -> ERC reduces to inverse-vol: vols 0.2, 0.1 -> 1/3, 2/3
    cov = [[0.04, 0.0], [0.0, 0.01]]
    w = weigh_risk_parity(["A", "B"], cov)
    assert w["A"] == pytest.approx(1 / 3, abs=1e-2)
    assert w["B"] == pytest.approx(2 / 3, abs=1e-2)
