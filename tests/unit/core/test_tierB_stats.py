"""Tier B statistics indicators — behavioural correctness tests (TDD).

Covers: std_error, std_error_bands, rank_correlation, correl_log (statistics.py)
"""

import math

import pytest

from vike_trader_app.core.indicators.statistics import (
    std_error,
    std_error_bands,
    rank_correlation,
    correl_log,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _ramp(n=50, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _const(n=50, val=100.0):
    return [val] * n


def _tail(lst, n=10):
    return [v for v in lst[-n:] if v is not None]


def _finite(v):
    return v is not None and not math.isnan(v) and not math.isinf(v)


# ── std_error ─────────────────────────────────────────────────────────────────

class TestStdError:
    def test_length_aligned(self):
        closes = _ramp(60)
        out = std_error(closes, period=20)
        assert len(out) == 60

    def test_warmup_none(self):
        closes = _ramp(40)
        out = std_error(closes, period=20)
        assert out[0] is None

    def test_nonnegative(self):
        """Standard error must always be >= 0."""
        closes = [100.0 + math.sin(i / 3.0) + i * 0.1 for i in range(80)]
        out = std_error(closes, period=20)
        defined = [v for v in out if v is not None]
        assert defined
        assert all(v >= 0.0 for v in defined), f"Negative std_error: {min(defined)}"

    def test_zero_on_perfect_line(self):
        """On a perfect linear series, the regression fits exactly → std_error = 0."""
        closes = _ramp(60, step=2.0)
        out = std_error(closes, period=20)
        tail = _tail(out)
        assert tail
        assert all(v == pytest.approx(0.0, abs=1e-9) for v in tail)

    def test_positive_on_noisy_series(self):
        """On a noisy series, std_error must be > 0."""
        import random
        rng = random.Random(42)
        closes = [100.0 + rng.gauss(0, 2.0) for _ in range(80)]
        out = std_error(closes, period=20)
        tail = _tail(out)
        assert tail and all(v > 0.0 for v in tail)

    def test_finite_tail(self):
        closes = [100.0 + math.sin(i / 4.0) for i in range(80)]
        out = std_error(closes, period=20)
        tail = _tail(out)
        assert tail and all(_finite(v) for v in tail)


# ── std_error_bands ───────────────────────────────────────────────────────────

class TestStdErrorBands:
    def test_returns_three_lines(self):
        closes = _ramp(60)
        result = std_error_bands(closes, period=20, mult=2.0)
        assert len(result) == 3  # (upper, mid, lower)

    def test_length_aligned(self):
        closes = _ramp(60)
        upper, mid, lower = std_error_bands(closes, period=20, mult=2.0)
        assert len(upper) == 60
        assert len(mid) == 60
        assert len(lower) == 60

    def test_warmup_none(self):
        closes = _ramp(40)
        upper, mid, lower = std_error_bands(closes, period=20, mult=2.0)
        assert upper[0] is None
        assert mid[0] is None
        assert lower[0] is None

    def test_upper_gt_mid_gt_lower(self):
        """On any non-perfect series: upper > mid > lower at every defined point."""
        closes = [100.0 + 5 * math.sin(i / 5.0) + i * 0.1 for i in range(80)]
        upper, mid, lower = std_error_bands(closes, period=20, mult=2.0)
        for i in range(len(closes)):
            if upper[i] is not None and lower[i] is not None:
                assert upper[i] > mid[i], f"upper <= mid at i={i}"
                assert mid[i] > lower[i], f"mid <= lower at i={i}"

    def test_flat_series_bands_equal(self):
        """On a perfect ramp (std_error=0), all three bands equal linearreg."""
        closes = _ramp(60, step=1.0)
        upper, mid, lower = std_error_bands(closes, period=20, mult=2.0)
        tail_upper = _tail(upper)
        tail_mid = _tail(mid)
        tail_lower = _tail(lower)
        assert tail_upper and tail_mid and tail_lower
        for u, m, lo in zip(tail_upper, tail_mid, tail_lower):
            assert u == pytest.approx(m, abs=1e-9)
            assert m == pytest.approx(lo, abs=1e-9)

    def test_mid_equals_linearreg(self):
        """mid should equal the linearreg value at each point."""
        from vike_trader_app.core.indicators.statistics import linearreg
        closes = [100.0 + 5 * math.sin(i / 5.0) + i * 0.1 for i in range(80)]
        period = 20
        upper, mid, lower = std_error_bands(closes, period=period, mult=2.0)
        lr = linearreg(closes, period)
        for i in range(len(closes)):
            if mid[i] is not None and lr[i] is not None:
                assert mid[i] == pytest.approx(lr[i], rel=1e-9)


# ── rank_correlation ──────────────────────────────────────────────────────────

class TestRankCorrelation:
    def test_length_aligned(self):
        closes = _ramp(60)
        out = rank_correlation(closes, period=14)
        assert len(out) == 60

    def test_warmup_none(self):
        closes = _ramp(40)
        out = rank_correlation(closes, period=14)
        assert out[0] is None

    def test_strictly_rising_series_near_plus100(self):
        """Spearman corr of a strictly rising series vs time index should be ≈ +100."""
        closes = _ramp(60, step=1.0)
        out = rank_correlation(closes, period=14)
        tail = _tail(out)
        assert tail, "No defined values in tail"
        assert all(v > 95.0 for v in tail), (
            f"rank_correlation of rising series not near +100: {tail}"
        )

    def test_strictly_falling_series_near_minus100(self):
        """Spearman corr of a strictly falling series vs time index should be ≈ -100."""
        closes = _ramp(60, start=200.0, step=-1.0)
        out = rank_correlation(closes, period=14)
        tail = _tail(out)
        assert tail
        assert all(v < -95.0 for v in tail), (
            f"rank_correlation of falling series not near -100: {tail}"
        )

    def test_range_minus100_plus100(self):
        """Output must always be in [-100, 100]."""
        closes = [100.0 + math.sin(i / 3.0) + i * 0.05 for i in range(80)]
        out = rank_correlation(closes, period=14)
        defined = [v for v in out if v is not None]
        assert defined
        assert all(-100.0 <= v <= 100.0 for v in defined), (
            f"rank_correlation out of [-100,100]: min={min(defined):.2f} max={max(defined):.2f}"
        )

    def test_output_name_rci(self):
        from vike_trader_app.core.indicators import base
        spec = base.get("rank_correlation")
        assert spec.outputs == ["rci"]

    def test_finite_tail(self):
        closes = [100.0 + math.sin(i / 4.0) for i in range(80)]
        out = rank_correlation(closes, period=14)
        tail = _tail(out)
        assert tail and all(_finite(v) for v in tail)


# ── correl_log ────────────────────────────────────────────────────────────────

class TestCorrelLog:
    def test_length_aligned(self):
        closes = _ramp(80)
        bench = _ramp(80, start=50.0, step=0.5)
        out = correl_log(closes, bench, period=30)
        assert len(out) == 80

    def test_warmup_none(self):
        closes = _ramp(60)
        bench = _ramp(60, start=50.0, step=0.5)
        out = correl_log(closes, bench, period=30)
        assert out[0] is None

    def test_identical_series_approx_one(self):
        """Pearson corr of a series' log returns with itself = 1."""
        closes = [100.0 + math.sin(i / 5.0) + i * 0.2 for i in range(80)]
        out = correl_log(closes, closes, period=30)
        tail = _tail(out)
        assert tail, "No defined values in tail"
        assert all(v == pytest.approx(1.0, rel=1e-5) for v in tail), (
            f"correl_log(self) not ≈1: {tail}"
        )

    def test_bounded_minus1_plus1(self):
        """Output must be in [-1, 1]."""
        a = [100.0 + 5 * math.sin(i / 4.0) + i * 0.1 for i in range(80)]
        b = [80.0 + 3 * math.cos(i / 5.0) + i * 0.05 for i in range(80)]
        out = correl_log(a, b, period=30)
        defined = [v for v in out if v is not None]
        assert defined
        assert all(-1.0 - 1e-9 <= v <= 1.0 + 1e-9 for v in defined)

    def test_perfectly_correlated_returns_approx_one(self):
        """Two series with identical log returns → correl_log ≈ 1."""
        import random
        rng = random.Random(7)
        log_rets = [rng.gauss(0.001, 0.02) for _ in range(80)]
        a = [100.0]
        b = [50.0]
        for r in log_rets:
            a.append(a[-1] * math.exp(r))
            b.append(b[-1] * math.exp(r))
        out = correl_log(a, b, period=30)
        tail = _tail(out)
        assert tail
        assert all(v == pytest.approx(1.0, rel=1e-4) for v in tail)

    def test_inputs_are_close_and_benchmark(self):
        from vike_trader_app.core.indicators import base
        spec = base.get("correl_log")
        assert spec.inputs == ["close", "benchmark"]

    def test_finite_tail(self):
        a = [100.0 + math.sin(i / 4.0) + i * 0.1 for i in range(80)]
        b = [80.0 + math.cos(i / 5.0) + i * 0.05 for i in range(80)]
        out = correl_log(a, b, period=30)
        tail = _tail(out)
        assert tail and all(_finite(v) for v in tail)


# ── registration ─────────────────────────────────────────────────────────────

class TestRegistration:
    def test_all_registered(self):
        from vike_trader_app.core.indicators import base
        names = {s.name for s in base.list_indicators(category="statistics")}
        expected = {"std_error", "std_error_bands", "rank_correlation", "correl_log"}
        missing = expected - names
        assert not missing, f"Not registered: {missing}"

    def test_categories(self):
        from vike_trader_app.core.indicators import base
        for name in ("std_error", "std_error_bands", "rank_correlation", "correl_log"):
            assert base.get(name).category == "statistics"
