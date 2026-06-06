"""Correctness tests for Tier A momentum indicators (Task 2)."""

import math
import pytest

from vike_trader_app.core.indicators.momentum import (
    mom,
    rocp,
    rocr,
    rocr100,
    ppo,
    apo,
    cmo,
    trix,
    tsi,
    dpo,
    aroon,
    aroonosc,
    adxr,
    bop,
    stochf,
    stochrsi,
    ultosc,
    kst,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def finite(v):
    return v is not None and not math.isnan(v) and not math.isinf(v)


def ramp(n, start=10.0, step=1.0):
    """Strictly rising series: [10, 11, 12, ...]."""
    return [start + i * step for i in range(n)]


def flat(n, value=100.0):
    return [value] * n


# ---------------------------------------------------------------------------
# mom
# ---------------------------------------------------------------------------

class TestMom:
    def test_exact_diffs_on_ramp(self):
        values = ramp(20, step=2.0)   # [10, 12, 14, ...]
        period = 5
        out = mom(values, period)
        # warm-up: first `period` entries are None
        assert all(v is None for v in out[:period])
        # at index 5: values[5] - values[0] = 20 - 10 = 10
        assert out[period] == pytest.approx(10.0)
        # at index 10: values[10] - values[5] = 30 - 20 = 10
        assert out[10] == pytest.approx(10.0)

    def test_length_aligned(self):
        values = ramp(30)
        out = mom(values, 10)
        assert len(out) == 30

    def test_flat_series_is_zero(self):
        values = flat(20)
        out = mom(values, 5)
        assert all(v == pytest.approx(0.0) for v in out[5:])


# ---------------------------------------------------------------------------
# rocp
# ---------------------------------------------------------------------------

class TestRocp:
    def test_known_ratio(self):
        # values[10]=20, values[5]=10  → (20-10)/10 = 1.0
        values = ramp(20, start=10.0, step=1.0)
        out = rocp(values, 5)
        assert all(v is None for v in out[:5])
        # values[5]=15, values[0]=10 → (15-10)/10 = 0.5
        assert out[5] == pytest.approx(0.5)

    def test_length(self):
        assert len(rocp(ramp(25), 5)) == 25


# ---------------------------------------------------------------------------
# rocr
# ---------------------------------------------------------------------------

class TestRocr:
    def test_known_ratio(self):
        # rocr = v[i] / v[i-p]
        values = [10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
        out = rocr(values, 5)
        assert out[:5] == [None] * 5
        assert out[5] == pytest.approx(2.0)

    def test_length(self):
        assert len(rocr(ramp(20), 5)) == 20


# ---------------------------------------------------------------------------
# rocr100
# ---------------------------------------------------------------------------

class TestRocr100:
    def test_is_rocr_times_100(self):
        values = [10.0, 10.0, 10.0, 10.0, 10.0, 20.0]
        r = rocr(values, 5)
        r100 = rocr100(values, 5)
        for a, b in zip(r, r100):
            if a is None:
                assert b is None
            else:
                assert b == pytest.approx(a * 100.0)

    def test_length(self):
        assert len(rocr100(ramp(20), 5)) == 20


# ---------------------------------------------------------------------------
# ppo
# ---------------------------------------------------------------------------

class TestPpo:
    def test_zero_on_flat(self):
        values = flat(50)
        out = ppo(values, fast=12, slow=26)
        post = [v for v in out if v is not None]
        assert post, "should have post-warmup values"
        assert all(abs(v) < 1e-9 for v in post)

    def test_length(self):
        assert len(ppo(ramp(60), fast=12, slow=26)) == 60

    def test_positive_when_rising(self):
        # on a sharply rising ramp fast EMA > slow EMA → ppo > 0
        values = ramp(60, step=5.0)
        out = ppo(values, fast=5, slow=20)
        post = [v for v in out if v is not None]
        assert all(v > 0 for v in post)


# ---------------------------------------------------------------------------
# apo
# ---------------------------------------------------------------------------

class TestApo:
    def test_zero_on_flat(self):
        values = flat(50)
        out = apo(values, fast=12, slow=26)
        post = [v for v in out if v is not None]
        assert all(abs(v) < 1e-9 for v in post)

    def test_length(self):
        assert len(apo(ramp(60), fast=12, slow=26)) == 60


# ---------------------------------------------------------------------------
# cmo
# ---------------------------------------------------------------------------

class TestCmo:
    def test_rising_series_near_100(self):
        # Strictly rising: all ups, no downs → cmo ≈ 100
        values = ramp(40, step=1.0)
        out = cmo(values, 14)
        post = [v for v in out if v is not None]
        assert all(v == pytest.approx(100.0) for v in post)

    def test_flat_is_zero(self):
        values = flat(30)
        out = cmo(values, 14)
        post = [v for v in out if v is not None]
        assert all(abs(v) < 1e-9 for v in post)

    def test_length(self):
        assert len(cmo(ramp(40), 14)) == 40


# ---------------------------------------------------------------------------
# trix
# ---------------------------------------------------------------------------

class TestTrix:
    def test_length(self):
        assert len(trix(ramp(80), 18)) == 80

    def test_finite_tail(self):
        out = trix(ramp(80), 18)
        tail = [v for v in out[-10:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)

    def test_flat_series_zero(self):
        out = trix(flat(80), 18)
        post = [v for v in out if v is not None]
        assert all(abs(v) < 1e-9 for v in post)


# ---------------------------------------------------------------------------
# tsi
# ---------------------------------------------------------------------------

class TestTsi:
    def test_length(self):
        assert len(tsi(ramp(80), long=25, short=13)) == 80

    def test_rising_series_near_100(self):
        # Strictly rising: momentum always positive → tsi near 100
        values = ramp(80, step=1.0)
        out = tsi(values, long=25, short=13)
        post = [v for v in out if v is not None]
        assert post
        assert all(v > 90 for v in post)

    def test_finite_tail(self):
        out = tsi(ramp(80), long=25, short=13)
        tail = [v for v in out[-10:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)


# ---------------------------------------------------------------------------
# dpo
# ---------------------------------------------------------------------------

class TestDpo:
    def test_length(self):
        assert len(dpo(ramp(50), 20)) == 50

    def test_finite_tail(self):
        out = dpo(ramp(50), 20)
        tail = [v for v in out[-10:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)

    def test_flat_is_zero(self):
        out = dpo(flat(50), 20)
        post = [v for v in out if v is not None]
        assert all(abs(v) < 1e-9 for v in post)


# ---------------------------------------------------------------------------
# aroon  (multi-output → tuple of two lists)
# ---------------------------------------------------------------------------

class TestAroon:
    def test_returns_two_aligned_lists(self):
        highs = ramp(30)
        lows = [h - 1 for h in highs]
        result = aroon(highs, lows, 14)
        assert isinstance(result, tuple)
        assert len(result) == 2
        up, down = result
        assert len(up) == 30
        assert len(down) == 30

    def test_up_100_on_strictly_rising(self):
        # Strictly rising highs → highest high is always the most recent bar
        highs = ramp(40, step=1.0)
        lows = [h - 0.5 for h in highs]
        up, down = aroon(highs, lows, 14)
        post_up = [v for v in up if v is not None]
        assert post_up
        assert all(v == pytest.approx(100.0) for v in post_up)

    def test_down_100_on_strictly_falling(self):
        # Strictly falling lows → lowest low is always the most recent bar
        highs = [100.0 - i * 0.5 for i in range(40)]
        lows = [h - 1.0 for h in highs]
        up, down = aroon(highs, lows, 14)
        post_down = [v for v in down if v is not None]
        assert post_down
        assert all(v == pytest.approx(100.0) for v in post_down)

    def test_values_in_0_100(self):
        highs = ramp(40)
        lows = [h - 1 for h in highs]
        up, down = aroon(highs, lows, 14)
        for v in up + down:
            if v is not None:
                assert 0.0 <= v <= 100.0


# ---------------------------------------------------------------------------
# aroonosc
# ---------------------------------------------------------------------------

class TestAroonosc:
    def test_length(self):
        highs = ramp(30)
        lows = [h - 1 for h in highs]
        assert len(aroonosc(highs, lows, 14)) == 30

    def test_up_minus_down(self):
        highs = ramp(30)
        lows = [h - 1 for h in highs]
        up, down = aroon(highs, lows, 14)
        osc = aroonosc(highs, lows, 14)
        for i, v in enumerate(osc):
            if v is None:
                assert up[i] is None or down[i] is None
            else:
                assert v == pytest.approx(up[i] - down[i])

    def test_rising_series_near_100(self):
        highs = ramp(40, step=1.0)
        lows = [h - 0.5 for h in highs]
        osc = aroonosc(highs, lows, 14)
        post = [v for v in osc if v is not None]
        assert all(v == pytest.approx(100.0) for v in post)


# ---------------------------------------------------------------------------
# adxr  (reuses adx)
# ---------------------------------------------------------------------------

class TestAdxr:
    def test_length(self):
        highs = ramp(60)
        lows = [h - 1 for h in highs]
        closes = [h - 0.5 for h in highs]
        assert len(adxr(highs, lows, closes, 14)) == 60

    def test_finite_tail(self):
        highs = ramp(60)
        lows = [h - 1 for h in highs]
        closes = [h - 0.5 for h in highs]
        out = adxr(highs, lows, closes, 14)
        tail = [v for v in out[-10:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)

    def test_warmup_none(self):
        n = 60
        highs = ramp(n)
        lows = [h - 1 for h in highs]
        closes = [h - 0.5 for h in highs]
        out = adxr(highs, lows, closes, 14)
        # At least the first 2*period values should be None
        assert all(v is None for v in out[:28])


# ---------------------------------------------------------------------------
# bop
# ---------------------------------------------------------------------------

class TestBop:
    def test_known_single_bar(self):
        # bop = (close - open) / (high - low)
        # bar: O=10, H=20, L=5, C=17
        # (17-10)/(20-5) = 7/15 ≈ 0.4667
        opens   = [10.0]
        highs   = [20.0]
        lows    = [5.0]
        closes  = [17.0]
        out = bop(opens, highs, lows, closes)
        assert len(out) == 1
        assert out[0] == pytest.approx(7.0 / 15.0)

    def test_zero_range_returns_zero(self):
        opens  = [10.0]
        highs  = [10.0]
        lows   = [10.0]
        closes = [10.0]
        out = bop(opens, highs, lows, closes)
        assert out[0] == pytest.approx(0.0)

    def test_no_none_values(self):
        # bop has no warm-up — every bar is defined
        opens  = ramp(20, start=9.0)
        highs  = ramp(20, start=11.0)
        lows   = ramp(20, start=8.0)
        closes = ramp(20, start=10.0)
        out = bop(opens, highs, lows, closes)
        assert all(v is not None for v in out)
        assert len(out) == 20

    def test_length(self):
        n = 30
        opens  = flat(n, 100.0)
        highs  = flat(n, 102.0)
        lows   = flat(n, 98.0)
        closes = flat(n, 101.0)
        out = bop(opens, highs, lows, closes)
        assert len(out) == n


# ---------------------------------------------------------------------------
# stochf  (multi-output → tuple of two lists)
# ---------------------------------------------------------------------------

class TestStochf:
    def test_returns_two_aligned_lists(self):
        highs  = ramp(30, start=11.0)
        lows   = ramp(30, start=9.0)
        closes = ramp(30, start=10.0)
        result = stochf(highs, lows, closes, k=5, d=3)
        assert isinstance(result, tuple)
        assert len(result) == 2
        k, d = result
        assert len(k) == 30
        assert len(d) == 30

    def test_rising_k_near_100(self):
        # close == high each bar → %K == 100 every defined bar
        # high=close (top of range), low = close - 2 (constant spread)
        closes = ramp(40, start=10.0)
        highs  = closes[:]            # close at the very top
        lows   = [c - 2.0 for c in closes]
        k, d = stochf(highs, lows, closes, k=5, d=3)
        post_k = [v for v in k if v is not None]
        assert post_k
        assert all(v == pytest.approx(100.0) for v in post_k)

    def test_finite_tail(self):
        highs  = ramp(40, start=11.0)
        lows   = ramp(40, start=9.0)
        closes = ramp(40, start=10.0)
        k, d = stochf(highs, lows, closes, k=5, d=3)
        tail_k = [v for v in k[-5:] if v is not None]
        tail_d = [v for v in d[-5:] if v is not None]
        assert tail_k and tail_d


# ---------------------------------------------------------------------------
# stochrsi  (multi-output → tuple of two lists)
# ---------------------------------------------------------------------------

class TestStochrsi:
    def test_returns_two_aligned_lists(self):
        values = ramp(80)
        result = stochrsi(values, rsi_p=14, k=14, d=3)
        assert isinstance(result, tuple)
        assert len(result) == 2
        k, d = result
        assert len(k) == 80
        assert len(d) == 80

    def test_finite_tail(self):
        values = ramp(80)
        k, d = stochrsi(values, rsi_p=14, k=14, d=3)
        tail_k = [v for v in k[-5:] if v is not None]
        tail_d = [v for v in d[-5:] if v is not None]
        assert tail_k and tail_d

    def test_k_in_0_100(self):
        values = [100 + 10 * math.sin(i / 5) for i in range(80)]
        k, d = stochrsi(values, rsi_p=14, k=14, d=3)
        for v in k:
            if v is not None:
                assert -1e-9 <= v <= 100.0 + 1e-9, f"stochrsi k={v} out of range"


# ---------------------------------------------------------------------------
# ultosc
# ---------------------------------------------------------------------------

class TestUltosc:
    def test_length(self):
        highs  = ramp(60, start=11.0)
        lows   = ramp(60, start=9.0)
        closes = ramp(60, start=10.0)
        out = ultosc(highs, lows, closes, p1=7, p2=14, p3=28)
        assert len(out) == 60

    def test_finite_tail(self):
        highs  = ramp(60, start=11.0)
        lows   = ramp(60, start=9.0)
        closes = ramp(60, start=10.0)
        out = ultosc(highs, lows, closes, p1=7, p2=14, p3=28)
        tail = [v for v in out[-5:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)

    def test_in_0_100(self):
        highs  = ramp(60, start=11.0)
        lows   = ramp(60, start=9.0)
        closes = ramp(60, start=10.0)
        out = ultosc(highs, lows, closes)
        for v in out:
            if v is not None:
                assert 0.0 <= v <= 100.0


# ---------------------------------------------------------------------------
# kst  (multi-output → tuple of two lists)
# ---------------------------------------------------------------------------

class TestKst:
    def test_returns_two_aligned_lists(self):
        values = ramp(100)
        result = kst(values)
        assert isinstance(result, tuple)
        assert len(result) == 2
        k_line, signal = result
        assert len(k_line) == 100
        assert len(signal) == 100

    def test_finite_tail(self):
        values = ramp(100)
        k_line, signal = kst(values)
        tail_k = [v for v in k_line[-5:] if v is not None]
        tail_s = [v for v in signal[-5:] if v is not None]
        assert tail_k and tail_s

    def test_warmup_none(self):
        # longest ROC period = 30, smoothed by SMA(15) → need ≥ 45 bars before first value
        values = ramp(100)
        k_line, _ = kst(values)
        assert all(v is None for v in k_line[:44])

    def test_length(self):
        values = ramp(100)
        k_line, signal = kst(values)
        assert len(k_line) == 100
        assert len(signal) == 100
