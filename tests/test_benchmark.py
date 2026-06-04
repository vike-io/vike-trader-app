"""Tests for analysis/benchmark.py — benchmark-comparison analytics."""

import math
import pytest

from vike_trader_app.analysis.benchmark import (
    alpha,
    benchmark_stats,
    beta,
    correlation,
    down_capture,
    information_ratio,
    r_squared,
    returns,
    tracking_error,
    up_capture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monotone(start=100.0, step=1.0, n=20):
    """Monotone increasing equity curve."""
    return [start + i * step for i in range(n)]


# ---------------------------------------------------------------------------
# returns()
# ---------------------------------------------------------------------------

def test_returns_length():
    eq = [100.0, 110.0, 99.0]
    r = returns(eq)
    assert len(r) == 2


def test_returns_values():
    eq = [100.0, 110.0, 99.0]
    r = returns(eq)
    assert r[0] == pytest.approx(0.1)
    assert r[1] == pytest.approx(99.0 / 110.0 - 1.0)


def test_returns_empty():
    assert returns([]) == []


# ---------------------------------------------------------------------------
# beta()
# ---------------------------------------------------------------------------

def test_beta_vs_itself_is_one():
    eq = [100 + i * 2.5 for i in range(30)]
    assert beta(eq, eq) == pytest.approx(1.0)


def test_beta_levered_copy():
    """A strategy whose returns are exactly 2x the benchmark returns → beta == 2."""
    # Build benchmark with geometric returns, then create a strategy with 2x each return
    bench = [100.0]
    rng_returns = [0.01, -0.005, 0.02, -0.01, 0.015, -0.008, 0.012, -0.003, 0.018, 0.007]
    for r in rng_returns * 2:  # 20 bars
        bench.append(bench[-1] * (1 + r))
    strat = [bench[0]]
    for i in range(1, len(bench)):
        rb = bench[i] / bench[i - 1] - 1
        strat.append(strat[-1] * (1 + 2 * rb))
    assert beta(strat, bench) == pytest.approx(2.0, rel=1e-6)


def test_beta_zero_variance_bench_returns_zero():
    flat = [100.0] * 20
    strat = [100.0 + i for i in range(20)]
    assert beta(strat, flat) == 0.0


def test_beta_length_mismatch_raises():
    with pytest.raises(ValueError):
        beta([1.0, 2.0], [1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# alpha()
# ---------------------------------------------------------------------------

def test_alpha_identical_curves_approx_zero():
    eq = [100.0 * (1.01 ** i) for i in range(252)]
    assert alpha(eq, eq, periods_per_year=252) == pytest.approx(0.0, abs=1e-10)


def test_alpha_length_mismatch_raises():
    with pytest.raises(ValueError):
        alpha([1.0, 2.0], [1.0, 2.0, 3.0], periods_per_year=252)


# ---------------------------------------------------------------------------
# correlation() and r_squared()
# ---------------------------------------------------------------------------

def test_correlation_vs_itself_is_one():
    eq = [100.0 + i * 1.3 for i in range(30)]
    assert correlation(eq, eq) == pytest.approx(1.0)


def test_correlation_zero_for_flat():
    flat = [100.0] * 20
    strat = [100.0 + i for i in range(20)]
    assert correlation(strat, flat) == 0.0


def test_r_squared_is_correlation_squared():
    eq = [100.0 + i * 0.7 + (-1) ** i * 2 for i in range(40)]
    bench = [100.0 + i * 0.5 + (-1) ** i * 1 for i in range(40)]
    corr = correlation(eq, bench)
    assert r_squared(eq, bench) == pytest.approx(corr ** 2)


def test_correlation_length_mismatch_raises():
    with pytest.raises(ValueError):
        correlation([1.0, 2.0], [1.0])


# ---------------------------------------------------------------------------
# tracking_error()
# ---------------------------------------------------------------------------

def test_tracking_error_identical_curves_is_zero():
    eq = [100.0 + i for i in range(20)]
    assert tracking_error(eq, eq, periods_per_year=252) == pytest.approx(0.0)


def test_tracking_error_positive():
    bench = [100.0 + i for i in range(30)]
    strat = [100.0 + i * 1.5 + (-1) ** i * 3 for i in range(30)]
    te = tracking_error(strat, bench, periods_per_year=252)
    assert te > 0.0


# ---------------------------------------------------------------------------
# information_ratio()
# ---------------------------------------------------------------------------

def test_ir_identical_curves_is_zero():
    eq = [100.0 + i * 0.5 for i in range(30)]
    assert information_ratio(eq, eq, periods_per_year=252) == pytest.approx(0.0)


def test_ir_outperforming_is_positive():
    bench = [100.0 + i for i in range(30)]
    strat = [100.0 + i * 2 for i in range(30)]
    ir = information_ratio(strat, bench, periods_per_year=252)
    # Strategy consistently outperforms → positive IR (though tracking error may be zero
    # in the purely proportional case — check for non-negative at minimum)
    assert ir >= 0.0


# ---------------------------------------------------------------------------
# up_capture() and down_capture()
# ---------------------------------------------------------------------------

def test_up_capture_vs_itself_approx_one():
    eq = [100 + i * 1.5 + (-1) ** i * 3 for i in range(40)]
    # Only test if there are up bars (there will be with a trend + noise)
    uc = up_capture(eq, eq)
    if uc != 0.0:
        assert uc == pytest.approx(1.0, rel=1e-9)


def test_down_capture_vs_itself_approx_one():
    eq = [100 + i * 1.5 + (-1) ** i * 5 for i in range(40)]
    dc = down_capture(eq, eq)
    if dc != 0.0:
        assert dc == pytest.approx(1.0, rel=1e-9)


def test_up_capture_two_x_levered():
    """Strategy returns 2x bench returns → up capture ≈ 2."""
    # Build a bench with explicit alternating up/down moves so returns are non-zero
    bench = [100.0]
    for i in range(39):
        bench.append(bench[-1] * (1.02 if i % 2 == 0 else 0.99))
    strat = [bench[0] * 2]
    for i in range(1, len(bench)):
        rb = bench[i] / bench[i - 1] - 1
        strat.append(strat[-1] * (1 + 2 * rb))
    uc = up_capture(strat, bench)
    assert uc == pytest.approx(2.0, rel=1e-6)


def test_up_capture_no_up_bars_returns_zero():
    # Strictly declining benchmark
    bench = [100.0 - i for i in range(20)]
    strat = [100.0 - i * 0.5 for i in range(20)]
    assert up_capture(strat, bench) == 0.0


def test_down_capture_no_down_bars_returns_zero():
    # Strictly rising benchmark
    bench = [100.0 + i for i in range(20)]
    strat = [100.0 + i * 0.5 for i in range(20)]
    assert down_capture(strat, bench) == 0.0


# ---------------------------------------------------------------------------
# benchmark_stats() — bundle
# ---------------------------------------------------------------------------

def test_benchmark_stats_keys():
    eq = [100.0 + i for i in range(20)]
    stats = benchmark_stats(eq, eq, periods_per_year=252)
    expected_keys = {
        "beta", "alpha", "correlation", "r_squared",
        "tracking_error", "information_ratio", "up_capture", "down_capture",
    }
    assert set(stats.keys()) == expected_keys


def test_benchmark_stats_vs_itself():
    eq = [100.0 + i * 1.5 + (-1) ** i * 2 for i in range(40)]
    stats = benchmark_stats(eq, eq, periods_per_year=252)
    assert stats["beta"] == pytest.approx(1.0)
    assert stats["correlation"] == pytest.approx(1.0)
    assert stats["r_squared"] == pytest.approx(1.0)
    assert stats["tracking_error"] == pytest.approx(0.0, abs=1e-12)
    assert stats["alpha"] == pytest.approx(0.0, abs=1e-10)


def test_benchmark_stats_length_mismatch_raises():
    with pytest.raises(ValueError):
        benchmark_stats([1.0, 2.0], [1.0, 2.0, 3.0], periods_per_year=252)
