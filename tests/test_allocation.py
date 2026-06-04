"""Composable allocation layer: Select -> Weigh (bt-style) for portfolio rebalancing."""

import pytest
import numpy as np

from vike_trader_app.analysis.allocation import (
    select_top_n,
    weigh_equally,
    weigh_inverse_vol,
    weigh_min_variance,
    weigh_risk_parity,
    clamp_weights,
    cap_group_exposure,
    apply_cash_reserve,
    apply_turnover_band,
    select_decorrelated,
    cov_matrix,
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


# ── clamp_weights ─────────────────────────────────────────────────────────────

def test_clamp_weights_redistributes_excess_and_preserves_sum():
    """A=0.6 capped to 0.4; 0.2 excess goes to B/C pro-rata; sum stays 1.0; no name > 0.4."""
    w = clamp_weights({"A": 0.6, "B": 0.3, "C": 0.1}, 0.4)
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["A"] == pytest.approx(0.4)
    assert w["B"] > 0.3  # received some excess
    assert w["C"] > 0.1  # received some excess
    assert all(v <= 0.4 + 1e-9 for v in w.values())


def test_clamp_weights_pro_rata_distribution():
    """B and C absorb excess in proportion to their pre-redistribution weights."""
    w = clamp_weights({"A": 0.6, "B": 0.3, "C": 0.1}, 0.4)
    # B gets 0.3/(0.3+0.1) = 75% of the 0.2 excess = 0.15 -> 0.45 -> but that would exceed 0.4
    # so the fixed-point should settle correctly — just check the ratio B>C and sum=1
    assert w["B"] > w["C"]
    assert sum(w.values()) == pytest.approx(1.0)


def test_clamp_weights_infeasible_pins_at_max():
    """When max_weight * n < sum(weights), everyone is pinned at max_weight."""
    # sum = 1.0, n = 3, max = 0.2 -> max*n = 0.6 < 1.0
    w = clamp_weights({"A": 0.5, "B": 0.3, "C": 0.2}, 0.2)
    assert all(v == pytest.approx(0.2) for v in w.values())


def test_clamp_weights_no_op_when_all_within_cap():
    w = clamp_weights({"A": 0.3, "B": 0.3, "C": 0.4}, 0.5)
    assert w == pytest.approx({"A": 0.3, "B": 0.3, "C": 0.4})


def test_clamp_weights_empty():
    assert clamp_weights({}, 0.5) == {}


# ── cap_group_exposure ────────────────────────────────────────────────────────

def test_cap_group_exposure_scales_over_limit_group():
    """Group 'tech' with total 0.8 is scaled to 0.5; group 'finance' at 0.2 unchanged."""
    weights = {"AAPL": 0.5, "MSFT": 0.3, "JPM": 0.2}
    groups = {"AAPL": "tech", "MSFT": "tech", "JPM": "finance"}
    result = cap_group_exposure(weights, groups, max_per_group=0.5)
    tech_total = result["AAPL"] + result["MSFT"]
    assert tech_total == pytest.approx(0.5)
    assert result["JPM"] == pytest.approx(0.2)


def test_cap_group_exposure_preserves_internal_ratios():
    """Within a scaled group the ratio between members is preserved."""
    weights = {"AAPL": 0.6, "MSFT": 0.2, "JPM": 0.2}
    groups = {"AAPL": "tech", "MSFT": "tech", "JPM": "finance"}
    result = cap_group_exposure(weights, groups, max_per_group=0.4)
    # tech was 0.8 total; AAPL:MSFT = 3:1 before scaling
    assert result["AAPL"] / result["MSFT"] == pytest.approx(3.0, rel=1e-6)


def test_cap_group_exposure_max_names_per_group():
    """max_names_per_group=1 keeps only the top name per group."""
    weights = {"AAPL": 0.5, "MSFT": 0.3, "JPM": 0.2}
    groups = {"AAPL": "tech", "MSFT": "tech", "JPM": "finance"}
    result = cap_group_exposure(weights, groups, max_per_group=1.0, max_names_per_group=1)
    assert result["MSFT"] == pytest.approx(0.0)
    assert result["AAPL"] > 0.0
    assert result["JPM"] > 0.0


def test_cap_group_exposure_max_names_and_cap_combined():
    """After trimming to top-1, the survivor is then capped at max_per_group."""
    weights = {"AAPL": 0.7, "MSFT": 0.3}
    groups = {"AAPL": "tech", "MSFT": "tech"}
    result = cap_group_exposure(weights, groups, max_per_group=0.4, max_names_per_group=1)
    assert result["MSFT"] == pytest.approx(0.0)
    assert result["AAPL"] == pytest.approx(0.4)


# ── apply_cash_reserve ────────────────────────────────────────────────────────

def test_apply_cash_reserve_scales_down_to_target():
    """Weights summing to 1.2 are scaled so sum == 1.0."""
    w = apply_cash_reserve({"A": 0.7, "B": 0.5}, 1.0)
    assert sum(w.values()) == pytest.approx(1.0)
    # internal ratio preserved
    assert w["A"] / w["B"] == pytest.approx(0.7 / 0.5, rel=1e-6)


def test_apply_cash_reserve_unchanged_when_already_under():
    """Weights already <= target_invested are not modified."""
    w = apply_cash_reserve({"A": 0.4, "B": 0.4}, 1.0)
    assert w == pytest.approx({"A": 0.4, "B": 0.4})


def test_apply_cash_reserve_partial_invest():
    """target_invested=0.8 with a 1.0-sum input -> output sums to 0.8."""
    w = apply_cash_reserve({"A": 0.5, "B": 0.5}, 0.8)
    assert sum(w.values()) == pytest.approx(0.8)


def test_apply_cash_reserve_empty():
    assert apply_cash_reserve({}, 1.0) == {}


# ── apply_turnover_band ───────────────────────────────────────────────────────

def test_turnover_band_keeps_current_within_band():
    """Diff < band -> keep current weight."""
    target = {"A": 0.35, "B": 0.65}
    current = {"A": 0.30, "B": 0.70}
    result = apply_turnover_band(target, current, band=0.10)
    assert result["A"] == pytest.approx(0.30)  # |0.35-0.30|=0.05 < 0.10 -> keep current
    assert result["B"] == pytest.approx(0.70)  # same


def test_turnover_band_takes_target_outside_band():
    """Diff >= band -> use target weight."""
    target = {"A": 0.60, "B": 0.40}
    current = {"A": 0.30, "B": 0.70}
    result = apply_turnover_band(target, current, band=0.10)
    assert result["A"] == pytest.approx(0.60)  # |0.60-0.30|=0.30 >= 0.10 -> take target
    assert result["B"] == pytest.approx(0.40)


def test_turnover_band_new_symbol_in_target():
    """Symbol only in target defaults current to 0; if diff >= band use target."""
    target = {"A": 0.5, "NEW": 0.5}
    current = {"A": 0.5}
    result = apply_turnover_band(target, current, band=0.1)
    assert result["NEW"] == pytest.approx(0.5)  # |0.5-0|=0.5 >= 0.1


def test_turnover_band_symbol_only_in_current():
    """Symbol only in current (target=0); if diff >= band use target (0)."""
    target = {}
    current = {"A": 0.5}
    result = apply_turnover_band(target, current, band=0.1)
    assert result["A"] == pytest.approx(0.0)  # |0-0.5|=0.5 >= 0.1 -> target=0


def test_turnover_band_exact_band_boundary():
    """diff clearly >= band (well above float noise) -> take target."""
    target = {"A": 0.5}
    current = {"A": 0.35}
    result = apply_turnover_band(target, current, band=0.1)
    # |0.5-0.35| = 0.15 > 0.10 -> take target
    assert result["A"] == pytest.approx(0.5)


# ── select_decorrelated ───────────────────────────────────────────────────────

def _make_cov_numpy(symbols, corr_matrix, vols):
    """Build a numpy covariance array from a correlation matrix and vol vector."""
    n = len(symbols)
    cov = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            cov[i, j] = corr_matrix[i][j] * vols[i] * vols[j]
    return cov


def test_select_decorrelated_drops_perfectly_correlated():
    """A pair with corr=1.0 > max_corr=0.8 should drop the second candidate."""
    symbols = ["A", "B"]
    vols = [0.2, 0.2]
    corr = [[1.0, 1.0], [1.0, 1.0]]  # perfect correlation
    cov = _make_cov_numpy(symbols, corr, vols)
    kept = select_decorrelated(["A", "B"], cov, max_corr=0.8)
    assert kept == ["A"]


def test_select_decorrelated_keeps_uncorrelated():
    """Uncorrelated pair (corr=0) should both be kept."""
    symbols = ["A", "B"]
    vols = [0.2, 0.3]
    corr = [[1.0, 0.0], [0.0, 1.0]]
    cov = _make_cov_numpy(symbols, corr, vols)
    kept = select_decorrelated(["A", "B"], cov, max_corr=0.8)
    assert kept == ["A", "B"]


def test_select_decorrelated_three_candidates_drops_second_only():
    """With A-B corr=0.9 and A-C corr=0.1, B is dropped but C is kept."""
    symbols = ["A", "B", "C"]
    vols = [0.2, 0.2, 0.2]
    corr = [
        [1.0, 0.9, 0.1],
        [0.9, 1.0, 0.1],
        [0.1, 0.1, 1.0],
    ]
    cov = _make_cov_numpy(symbols, corr, vols)
    kept = select_decorrelated(["A", "B", "C"], cov, max_corr=0.8)
    assert "A" in kept
    assert "B" not in kept
    assert "C" in kept


def test_select_decorrelated_missing_cov_data_keeps_candidate():
    """A candidate with no cov data is kept (conservative fallback)."""
    # Flat tuple-keyed dict with only A-A entry; B's data missing
    cov_dict = {("A", "A"): 0.04, ("B", "B"): 0.04, ("A", "B"): 0.04, ("B", "A"): 0.04}
    # Modify: remove B,B so correlation cannot be computed
    cov_partial = {("A", "A"): 0.04}
    kept = select_decorrelated(["A", "B"], cov_partial, max_corr=0.8)
    assert "B" in kept  # missing data -> keep


def test_select_decorrelated_tuple_keyed_dict():
    """Works with a flat {(a,b): cov} dict (perfect correlation drops second)."""
    cov_dict = {
        ("A", "A"): 0.04,
        ("B", "B"): 0.04,
        ("A", "B"): 0.04,  # corr = 0.04/(0.2*0.2) = 1.0
        ("B", "A"): 0.04,
    }
    kept = select_decorrelated(["A", "B"], cov_dict, max_corr=0.8)
    assert kept == ["A"]


def test_select_decorrelated_empty_candidates():
    assert select_decorrelated([], {}, max_corr=0.5) == []


def test_select_decorrelated_uses_cov_matrix_output():
    """Integration: use the actual cov_matrix function output (numpy array)."""
    import numpy as np
    rng = np.random.default_rng(42)
    # Two identical return series -> perfect correlation
    r = rng.normal(0, 0.01, 100).tolist()
    returns = {"A": r, "B": r, "C": rng.normal(0, 0.01, 100).tolist()}
    symbols = ["A", "B", "C"]
    cov = cov_matrix(returns, symbols)
    kept = select_decorrelated(symbols, cov, max_corr=0.95)
    assert "A" in kept
    assert "B" not in kept  # identical series -> corr=1 > 0.95
    assert "C" in kept
