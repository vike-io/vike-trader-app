"""Tests for analysis/montecarlo.py — Monte Carlo resampling analytics."""

import pytest

from vike_trader_app.analysis.montecarlo import (
    confidence_bands,
    mc_resample,
    mc_summary,
    risk_of_ruin,
)


START = 10_000.0


# ---------------------------------------------------------------------------
# mc_resample()
# ---------------------------------------------------------------------------

def test_mc_resample_returns_correct_keys():
    result = mc_resample([10, 20, -5, 15], start_equity=START, n_sims=50, seed=1)
    assert set(result.keys()) == {"terminal", "max_drawdowns", "curves_sample"}


def test_mc_resample_terminal_length():
    result = mc_resample([10, 20, 30], start_equity=START, n_sims=100, seed=0)
    assert len(result["terminal"]) == 100


def test_mc_resample_max_drawdowns_length():
    result = mc_resample([10, -5, 20], start_equity=START, n_sims=100, seed=0)
    assert len(result["max_drawdowns"]) == 100


def test_mc_resample_curves_sample_max_20():
    result = mc_resample([1.0] * 50, start_equity=START, n_sims=1000, seed=0)
    assert len(result["curves_sample"]) <= 20


def test_mc_resample_all_positive_pnls_no_drawdown():
    """All-positive PnLs should yield zero max drawdown in every path."""
    result = mc_resample([1, 2, 3, 4, 5], start_equity=START, n_sims=200, seed=3)
    assert all(dd == 0.0 for dd in result["max_drawdowns"])


def test_mc_resample_all_positive_terminal_above_start():
    pnls = [100.0] * 10
    result = mc_resample(pnls, start_equity=START, n_sims=100, seed=0)
    assert all(t > START for t in result["terminal"])


def test_mc_resample_deterministic_with_seed():
    kwargs = dict(start_equity=START, n_sims=200, seed=42)
    a = mc_resample([10, -3, 7, 2, -1], **kwargs)
    b = mc_resample([10, -3, 7, 2, -1], **kwargs)
    assert a["terminal"] == b["terminal"]
    assert a["max_drawdowns"] == b["max_drawdowns"]


def test_mc_resample_bootstrap_runs():
    result = mc_resample(
        [5, -2, 8, 1], start_equity=START, n_sims=50, seed=0, method="bootstrap"
    )
    assert len(result["terminal"]) == 50


def test_mc_resample_invalid_method_raises():
    with pytest.raises(ValueError):
        mc_resample([1, 2], start_equity=START, n_sims=10, method="bad")


def test_mc_resample_max_drawdowns_nonneg():
    result = mc_resample([5, -3, 2, -4, 6], start_equity=START, n_sims=100, seed=2)
    assert all(d >= 0 for d in result["max_drawdowns"])


def test_mc_resample_bootstrap_vs_shuffle_both_run():
    """Both methods produce n_sims results without error."""
    pnls = [10, -5, 7, 3, -2]
    r_sh = mc_resample(pnls, start_equity=START, n_sims=50, seed=0, method="shuffle")
    r_bs = mc_resample(pnls, start_equity=START, n_sims=50, seed=0, method="bootstrap")
    assert len(r_sh["terminal"]) == 50
    assert len(r_bs["terminal"]) == 50


def test_mc_resample_empty_pnls_returns_start():
    result = mc_resample([], start_equity=START, n_sims=10, seed=0)
    assert all(t == START for t in result["terminal"])
    assert all(d == 0.0 for d in result["max_drawdowns"])


# ---------------------------------------------------------------------------
# confidence_bands()
# ---------------------------------------------------------------------------

def test_confidence_bands_correct_keys():
    curves = [[100.0, 110.0, 105.0], [100.0, 95.0, 108.0]]
    bands = confidence_bands(curves)
    assert set(bands.keys()) == {0.05, 0.50, 0.95}


def test_confidence_bands_correct_length():
    curves = [[float(i + j) for i in range(10)] for j in range(5)]
    bands = confidence_bands(curves)
    for q_vals in bands.values():
        assert len(q_vals) == 10


def test_confidence_bands_p50_between_p5_and_p95():
    curves = [[100.0 + j + (i - 5) * 2.0 for j in range(20)] for i in range(11)]
    bands = confidence_bands(curves)
    for step in range(20):
        assert bands[0.05][step] <= bands[0.50][step] <= bands[0.95][step]


def test_confidence_bands_empty_curves():
    bands = confidence_bands([])
    assert bands[0.05] == []


# ---------------------------------------------------------------------------
# risk_of_ruin()
# ---------------------------------------------------------------------------

def test_risk_of_ruin_all_positive_is_zero():
    terminals = [START * 1.1, START * 1.5, START * 2.0]
    assert risk_of_ruin(terminals, ruin_level=START * 0.5) == 0.0


def test_risk_of_ruin_all_below_is_one():
    terminals = [START * 0.1, START * 0.2, START * 0.3]
    assert risk_of_ruin(terminals, ruin_level=START * 0.5) == 1.0


def test_risk_of_ruin_half():
    terminals = [START * 0.3, START * 0.4, START * 0.8, START * 1.2]
    assert risk_of_ruin(terminals, ruin_level=START * 0.5) == pytest.approx(0.5)


def test_risk_of_ruin_empty_is_zero():
    assert risk_of_ruin([], ruin_level=5000.0) == 0.0


# ---------------------------------------------------------------------------
# mc_summary()
# ---------------------------------------------------------------------------

def test_mc_summary_keys():
    summary = mc_summary([10, 20, -5, 15], start_equity=START, n_sims=200, seed=0)
    expected = {
        "terminal_p5", "terminal_p50", "terminal_p95",
        "max_dd_p50", "max_dd_p95",
        "prob_loss", "risk_of_ruin",
    }
    assert set(summary.keys()) == expected


def test_mc_summary_all_positive_pnls():
    """All-positive PnLs: terminal P5 > start, risk_of_ruin == 0, prob_loss == 0."""
    pnls = [50.0] * 20
    summary = mc_summary(pnls, start_equity=START, n_sims=500, seed=0, ruin_pct=0.5)
    assert summary["terminal_p5"] > START
    assert summary["risk_of_ruin"] == 0.0
    assert summary["prob_loss"] == 0.0


def test_mc_summary_deterministic():
    pnls = [10, -3, 7, 2, -1, 5, -4]
    a = mc_summary(pnls, start_equity=START, n_sims=300, seed=7)
    b = mc_summary(pnls, start_equity=START, n_sims=300, seed=7)
    assert a == b


def test_mc_summary_percentile_ordering():
    pnls = [5, -2, 8, 1, -3, 6]
    summary = mc_summary(pnls, start_equity=START, n_sims=500, seed=1)
    assert summary["terminal_p5"] <= summary["terminal_p50"] <= summary["terminal_p95"]
    assert summary["max_dd_p50"] <= summary["max_dd_p95"]


def test_mc_summary_max_dd_nonneg():
    pnls = [5, -3, 2, -4, 6]
    summary = mc_summary(pnls, start_equity=START, n_sims=200, seed=2)
    assert summary["max_dd_p50"] >= 0.0
    assert summary["max_dd_p95"] >= 0.0
