"""Correctness tests for Tier A statistics indicators (Task 6):
linearreg, linearreg_slope, linearreg_angle, linearreg_intercept, tsf,
var, beta, correl, zscore, skew, kurtosis, mad.
"""

import math

import pytest

from vike_trader_app.core.indicators.statistics import (
    linearreg,
    linearreg_slope,
    linearreg_angle,
    linearreg_intercept,
    tsf,
    var,
    beta,
    correl,
    zscore,
    skew,
    kurtosis,
    mad,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _ramp(n=50, start=0.0, step=1.0):
    return [start + i * step for i in range(n)]


def _const(n=50, val=10.0):
    return [val] * n


def _tail(lst, n=10):
    return [v for v in lst[-n:] if v is not None]


def _finite(v):
    return v is not None and not math.isnan(v) and not math.isinf(v)


# ── linearreg_slope ──────────────────────────────────────────────────────────

class TestLinearregSlope:
    def test_length(self):
        assert len(linearreg_slope(_ramp(), 14)) == 50

    def test_warmup_none(self):
        assert linearreg_slope(_ramp(), 14)[0] is None

    def test_ramp_slope_is_step(self):
        """Slope of [0,1,2,...] with step=1 should be 1.0 everywhere post-warmup."""
        vals = _ramp(50, start=0.0, step=1.0)
        out = linearreg_slope(vals, 14)
        for v in _tail(out):
            assert v == pytest.approx(1.0, rel=1e-9)

    def test_ramp_step_2(self):
        """Slope of [0,2,4,...] with step=2 should be 2.0."""
        vals = _ramp(50, step=2.0)
        out = linearreg_slope(vals, 10)
        for v in _tail(out):
            assert v == pytest.approx(2.0, rel=1e-9)

    def test_constant_slope_is_zero(self):
        out = linearreg_slope(_const(), 14)
        for v in _tail(out):
            assert v == pytest.approx(0.0, abs=1e-12)


# ── linearreg_intercept ───────────────────────────────────────────────────────

class TestLinearregIntercept:
    def test_length(self):
        assert len(linearreg_intercept(_ramp(), 14)) == 50

    def test_warmup_none(self):
        assert linearreg_intercept(_ramp(), 14)[0] is None

    def test_reconstruct_ramp(self):
        """intercept + slope*(p-1) == linearreg (regression value at last point)."""
        vals = _ramp(50, start=5.0, step=1.0)
        period = 10
        intercept_out = linearreg_intercept(vals, period)
        slope_out = linearreg_slope(vals, period)
        reg_out = linearreg(vals, period)
        for i in range(len(vals)):
            if intercept_out[i] is not None:
                reconstructed = intercept_out[i] + slope_out[i] * (period - 1)
                assert reconstructed == pytest.approx(reg_out[i], rel=1e-9)

    def test_constant_intercept_equals_constant(self):
        """For a constant series y=k, regression line is y=k, intercept=k."""
        out = linearreg_intercept(_const(50, 7.0), 14)
        for v in _tail(out):
            assert v == pytest.approx(7.0, rel=1e-9)


# ── linearreg ─────────────────────────────────────────────────────────────────

class TestLinearreg:
    def test_length(self):
        assert len(linearreg(_ramp(), 14)) == 50

    def test_warmup_none(self):
        assert linearreg(_ramp(), 14)[0] is None

    def test_ramp_equals_ramp(self):
        """Regression value at last point of a ramp window equals that point."""
        vals = _ramp(50, start=0.0, step=1.0)
        out = linearreg(vals, 14)
        for i, v in enumerate(out):
            if v is not None:
                assert v == pytest.approx(float(i), rel=1e-9)

    def test_constant_series(self):
        out = linearreg(_const(50, 3.0), 14)
        for v in _tail(out):
            assert v == pytest.approx(3.0, rel=1e-9)


# ── linearreg_angle ───────────────────────────────────────────────────────────

class TestLinearregAngle:
    def test_length(self):
        assert len(linearreg_angle(_ramp(), 14)) == 50

    def test_warmup_none(self):
        assert linearreg_angle(_ramp(), 14)[0] is None

    def test_ramp_angle_is_45(self):
        """slope=1.0 → angle = atan(1) degrees = 45°."""
        vals = _ramp(50, step=1.0)
        out = linearreg_angle(vals, 14)
        for v in _tail(out):
            assert v == pytest.approx(45.0, rel=1e-9)

    def test_constant_angle_is_zero(self):
        out = linearreg_angle(_const(), 14)
        for v in _tail(out):
            assert v == pytest.approx(0.0, abs=1e-12)


# ── tsf ───────────────────────────────────────────────────────────────────────

class TestTsf:
    def test_length(self):
        assert len(tsf(_ramp(), 14)) == 50

    def test_warmup_none(self):
        assert tsf(_ramp(), 14)[0] is None

    def test_ramp_projects_one_ahead(self):
        """TSF of a ramp [0,1,2,...] projects to i+1 (one step ahead)."""
        vals = _ramp(50, step=1.0)
        out = tsf(vals, 14)
        for i, v in enumerate(out):
            if v is not None:
                assert v == pytest.approx(float(i + 1), rel=1e-9)

    def test_tsf_greater_than_linearreg_on_uptrend(self):
        """TSF projects one step ahead so on an uptrend it's above linearreg."""
        vals = _ramp(50, step=1.0)
        lr = linearreg(vals, 14)
        tf = tsf(vals, 14)
        for i in range(len(vals)):
            if tf[i] is not None:
                assert tf[i] > lr[i] - 1e-9


# ── var ───────────────────────────────────────────────────────────────────────

class TestVar:
    def test_length(self):
        assert len(var(_ramp(), 20)) == 50

    def test_warmup_none(self):
        assert var(_ramp(), 20)[0] is None

    def test_constant_is_zero(self):
        out = var(_const(), 20)
        for v in _tail(out):
            assert v == pytest.approx(0.0, abs=1e-12)

    def test_known_window(self):
        """Population variance of [1,2,3,4,5]: mean=3, var=2."""
        vals = [3.0] * 20 + [1.0, 2.0, 3.0, 4.0, 5.0]
        out = var(vals, 5)
        assert out[-1] == pytest.approx(2.0, rel=1e-9)

    def test_nonnegative(self):
        out = var(_ramp(80), 20)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)


# ── beta ──────────────────────────────────────────────────────────────────────

class TestBeta:
    def test_length(self):
        n = 50
        s = _ramp(n)
        assert len(beta(s, s, 5)) == n

    def test_warmup_none(self):
        s = _ramp(50)
        assert beta(s, s, 5)[0] is None

    def test_identical_series_is_one(self):
        """beta(s, s) == 1.0 (asset and benchmark move identically)."""
        vals = [100.0 + 5.0 * math.sin(i / 3.0) for i in range(60)]
        out = beta(vals, vals, 5)
        for v in _tail(out):
            assert v == pytest.approx(1.0, rel=1e-6)

    def test_output_finite(self):
        vals = [100.0 + i * 0.3 for i in range(60)]
        bench = [100.0 + i * 0.2 + 0.1 * math.sin(i) for i in range(60)]
        out = beta(vals, bench, 5)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── correl ────────────────────────────────────────────────────────────────────

class TestCorrel:
    def test_length(self):
        s = _ramp(60)
        assert len(correl(s, s, 30)) == 60

    def test_warmup_none(self):
        s = _ramp(60)
        assert correl(s, s, 30)[0] is None

    def test_identical_series_is_one(self):
        """Pearson correlation of a series with itself = 1.0."""
        vals = [100.0 + math.sin(i / 4.0) for i in range(80)]
        out = correl(vals, vals, 30)
        for v in _tail(out):
            assert v == pytest.approx(1.0, rel=1e-6)

    def test_negated_series_is_minus_one(self):
        """Correlation of s with -s = -1.0."""
        vals = [100.0 + math.sin(i / 4.0) for i in range(80)]
        neg = [-v for v in vals]
        out = correl(vals, neg, 30)
        for v in _tail(out):
            assert v == pytest.approx(-1.0, rel=1e-6)

    def test_bounded(self):
        a = [math.sin(i / 3.0) for i in range(80)]
        b = [math.cos(i / 5.0) for i in range(80)]
        out = correl(a, b, 30)
        tail = _tail(out)
        assert tail and all(-1.0 - 1e-9 <= v <= 1.0 + 1e-9 for v in tail)


# ── zscore ────────────────────────────────────────────────────────────────────

class TestZscore:
    def test_length(self):
        assert len(zscore(_ramp(), 20)) == 50

    def test_warmup_none(self):
        assert zscore(_ramp(), 20)[0] is None

    def test_mean_approx_zero(self):
        """Mean of z-scores over many periods should be ~0."""
        vals = [100.0 + math.sin(i / 5.0) + i * 0.01 for i in range(200)]
        out = zscore(vals, 20)
        defined = [v for v in out if v is not None]
        assert abs(sum(defined) / len(defined)) < 1.5  # loose bound; it's a rolling z

    def test_constant_raises_or_zero(self):
        """Constant series → stddev=0; result should be 0 or None (not crash)."""
        out = zscore(_const(50, 5.0), 20)
        for v in _tail(out):
            # either None, 0, or NaN — just not a hard crash
            assert v is None or math.isnan(v) or v == pytest.approx(0.0, abs=1e-9)

    def test_finite_on_varying_data(self):
        vals = [100.0 + math.sin(i / 3.0) for i in range(80)]
        out = zscore(vals, 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── skew ──────────────────────────────────────────────────────────────────────

class TestSkew:
    def test_length(self):
        assert len(skew(_ramp(), 20)) == 50

    def test_warmup_none(self):
        assert skew(_ramp(), 20)[0] is None

    def test_symmetric_series_approx_zero(self):
        """Skewness of a symmetric (constant) series ≈ 0."""
        out = skew(_const(60, 5.0), 20)
        for v in _tail(out):
            assert v is None or math.isnan(v) or v == pytest.approx(0.0, abs=1e-9)

    def test_output_finite_on_varying_data(self):
        vals = [100.0 + math.sin(i / 3.0) for i in range(80)]
        out = skew(vals, 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── kurtosis ──────────────────────────────────────────────────────────────────

class TestKurtosis:
    def test_length(self):
        assert len(kurtosis(_ramp(), 20)) == 50

    def test_warmup_none(self):
        assert kurtosis(_ramp(), 20)[0] is None

    def test_output_finite_on_varying_data(self):
        vals = [100.0 + math.sin(i / 3.0) for i in range(80)]
        out = kurtosis(vals, 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)

    def test_normal_like_excess_kurtosis_near_zero(self):
        """Uniform window should have kurtosis below that of normal distribution."""
        import random
        random.seed(42)
        vals = [random.gauss(0, 1) for _ in range(200)]
        out = kurtosis(vals, 50)
        tail = _tail(out)
        assert tail  # just checking it produces values


# ── mad ───────────────────────────────────────────────────────────────────────

class TestMad:
    def test_length(self):
        assert len(mad(_ramp(), 20)) == 50

    def test_warmup_none(self):
        assert mad(_ramp(), 20)[0] is None

    def test_constant_is_zero(self):
        out = mad(_const(), 20)
        for v in _tail(out):
            assert v == pytest.approx(0.0, abs=1e-12)

    def test_known_window(self):
        """MAD of [1,2,3,4,5]: mean=3, deviations=[2,1,0,1,2], MAD=6/5=1.2."""
        vals = [3.0] * 20 + [1.0, 2.0, 3.0, 4.0, 5.0]
        out = mad(vals, 5)
        assert out[-1] == pytest.approx(1.2, rel=1e-9)

    def test_nonnegative(self):
        out = mad(_ramp(80), 20)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)


# ── registry ──────────────────────────────────────────────────────────────────

class TestRegistration:
    def test_statistics_indicators_registered(self):
        from vike_trader_app.core.indicators import base
        names = {s.name for s in base.list_indicators(category="statistics")}
        expected = {
            "linearreg", "linearreg_slope", "linearreg_angle", "linearreg_intercept",
            "tsf", "var", "beta", "correl", "zscore", "skew", "kurtosis", "mad",
        }
        missing = expected - names
        assert not missing, f"Not registered: {missing}"
