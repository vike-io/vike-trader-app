"""Correctness tests for Tier A overlap MAs (dema/tema/trima/smma/zlema/hma/vwma/t3/alma/midpoint/midprice)."""

import math

import pytest

# Import new indicators — these will fail until implemented
from vike_trader_app.core.indicators.overlap import (
    alma,
    dema,
    hma,
    midpoint,
    midprice,
    smma,
    sma,
    t3,
    tema,
    trima,
    vwma,
    zlema,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _const(n=50, val=10.0):
    return [val] * n


def _ramp(n=50, start=1.0, step=1.0):
    return [start + i * step for i in range(n)]


def _tail(lst, n=10):
    """Non-None values from the last ``n`` elements."""
    return [v for v in lst[-n:] if v is not None]


# ── dema ─────────────────────────────────────────────────────────────────────

class TestDema:
    def test_length(self):
        assert len(dema(_const(), 20)) == 50

    def test_warmup_none(self):
        out = dema(_const(), 20)
        assert out[0] is None

    def test_constant_converges(self):
        """dema of a constant series must equal the constant once warm."""
        out = dema(_const(100, 5.0), 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(5.0) for v in tail)

    def test_post_warmup_finite(self):
        out = dema(_ramp(100), 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── tema ─────────────────────────────────────────────────────────────────────

class TestTema:
    def test_length(self):
        assert len(tema(_const(), 20)) == 50

    def test_warmup_none(self):
        assert tema(_const(), 20)[0] is None

    def test_constant_converges(self):
        out = tema(_const(100, 7.5), 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(7.5) for v in tail)

    def test_post_warmup_finite(self):
        out = tema(_ramp(100), 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── trima ────────────────────────────────────────────────────────────────────

class TestTrima:
    def test_length(self):
        assert len(trima(_const(), 20)) == 50

    def test_warmup_none(self):
        assert trima(_const(), 20)[0] is None

    def test_constant_converges(self):
        out = trima(_const(100, 3.0), 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(3.0) for v in tail)

    def test_post_warmup_finite(self):
        out = trima(_ramp(100), 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── smma ─────────────────────────────────────────────────────────────────────

class TestSmma:
    def test_length(self):
        assert len(smma(_const(), 14)) == 50

    def test_warmup_none(self):
        assert smma(_const(), 14)[0] is None

    def test_constant_converges(self):
        out = smma(_const(100, 8.0), 14)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(8.0) for v in tail)

    def test_seed_value(self):
        """smma should be seeded with SMA of period bars."""
        vals = list(range(1, 30))  # 1..29
        out = smma(vals, 5)
        # seed at index 4: sma of [1,2,3,4,5] = 3.0
        assert out[4] == pytest.approx(3.0)


# ── zlema ────────────────────────────────────────────────────────────────────

class TestZlema:
    def test_length(self):
        assert len(zlema(_const(), 20)) == 50

    def test_warmup_none(self):
        assert zlema(_const(), 20)[0] is None

    def test_constant_converges(self):
        out = zlema(_const(100, 6.0), 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(6.0) for v in tail)

    def test_post_warmup_finite(self):
        out = zlema(_ramp(100), 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── hma ──────────────────────────────────────────────────────────────────────

class TestHma:
    def test_length(self):
        assert len(hma(_const(), 16)) == 50

    def test_warmup_none(self):
        assert hma(_const(), 16)[0] is None

    def test_constant_converges(self):
        out = hma(_const(100, 4.0), 16)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(4.0) for v in tail)

    def test_post_warmup_finite(self):
        out = hma(_ramp(100), 16)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── vwma ─────────────────────────────────────────────────────────────────────

class TestVwma:
    def test_length(self):
        n = 50
        assert len(vwma(_const(n), [1.0] * n, 20)) == n

    def test_warmup_none(self):
        n = 50
        assert vwma(_const(n), [1.0] * n, 20)[0] is None

    def test_equal_volumes_equals_sma(self):
        """vwma with all-equal volumes must equal sma exactly (post-warmup)."""
        n = 60
        closes = _ramp(n, 10.0, 0.5)
        volumes = [100.0] * n
        period = 20
        vw = vwma(closes, volumes, period)
        sm = sma(closes, period)
        # compare tail where both are defined
        for v_vwma, v_sma in zip(vw[-15:], sm[-15:]):
            if v_vwma is not None and v_sma is not None:
                assert v_vwma == pytest.approx(v_sma)

    def test_post_warmup_finite(self):
        n = 60
        closes = _ramp(n, 100.0, 1.0)
        volumes = [float(i + 1) for i in range(n)]
        out = vwma(closes, volumes, 20)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── t3 ───────────────────────────────────────────────────────────────────────

class TestT3:
    def test_length(self):
        assert len(t3(_const(), 20, 0.7)) == 50

    def test_warmup_none(self):
        assert t3(_const(), 20, 0.7)[0] is None

    def test_constant_converges(self):
        out = t3(_const(200, 9.0), 20, 0.7)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(9.0, abs=1e-6) for v in tail)

    def test_post_warmup_finite(self):
        out = t3(_ramp(200), 20, 0.7)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── alma ─────────────────────────────────────────────────────────────────────

class TestAlma:
    def test_length(self):
        assert len(alma(_const(), 20, 0.85, 6.0)) == 50

    def test_warmup_none(self):
        assert alma(_const(), 20, 0.85, 6.0)[0] is None

    def test_constant_converges(self):
        out = alma(_const(100, 5.0), 20, 0.85, 6.0)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(5.0, abs=1e-9) for v in tail)

    def test_post_warmup_finite(self):
        out = alma(_ramp(100), 20, 0.85, 6.0)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── midpoint ─────────────────────────────────────────────────────────────────

class TestMidpoint:
    def test_length(self):
        assert len(midpoint(_ramp(30), 14)) == 30

    def test_warmup_none(self):
        assert midpoint(_ramp(30), 14)[0] is None

    def test_known_window(self):
        """midpoint([1..14], period=14) = (max+min)/2 = (14+1)/2 = 7.5"""
        vals = list(range(1, 21))  # 1..20
        out = midpoint(vals, 14)
        # at index 13 the window is [1..14]: max=14, min=1, midpoint=7.5
        assert out[13] == pytest.approx(7.5)
        # at index 19 the window is [7..20]: max=20, min=7, midpoint=13.5
        assert out[19] == pytest.approx(13.5)

    def test_constant_series(self):
        out = midpoint(_const(30, 5.0), 14)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(5.0) for v in tail)


# ── midprice ─────────────────────────────────────────────────────────────────

class TestMidprice:
    def test_length(self):
        n = 30
        highs = [10.0 + i for i in range(n)]
        lows = [8.0 + i for i in range(n)]
        assert len(midprice(highs, lows, 14)) == n

    def test_warmup_none(self):
        n = 30
        highs = [10.0 + i for i in range(n)]
        lows = [8.0 + i for i in range(n)]
        assert midprice(highs, lows, 14)[0] is None

    def test_known_values(self):
        """(max_high_over_p + min_low_over_p) / 2 for a linear ramp."""
        n = 20
        highs = [float(10 + i) for i in range(n)]
        lows = [float(i) for i in range(n)]
        out = midprice(highs, lows, 5)
        # at index 4: window highs=[10,11,12,13,14], max=14; lows=[0,1,2,3,4], min=0 -> (14+0)/2=7
        assert out[4] == pytest.approx(7.0)
        # at index 19: window highs=[25,26,27,28,29], max=29; lows=[15,16,17,18,19], min=15 -> (29+15)/2=22
        assert out[19] == pytest.approx(22.0)

    def test_constant_bands(self):
        n = 30
        highs = [12.0] * n
        lows = [8.0] * n
        out = midprice(highs, lows, 14)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(10.0) for v in tail)


# ── registry smoke ────────────────────────────────────────────────────────────

class TestRegistration:
    def test_all_overlap_mas_registered(self):
        from vike_trader_app.core.indicators import base
        names = {s.name for s in base.list_indicators(category="overlap")}
        expected = {"dema", "tema", "trima", "smma", "zlema", "hma", "vwma", "t3", "alma", "midpoint", "midprice"}
        missing = expected - names
        assert not missing, f"Not registered: {missing}"
