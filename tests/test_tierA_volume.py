"""Correctness tests for Tier A volume indicators (Task 3)."""

import math
import pytest

from vike_trader_app.core.indicators.volume import (
    ad,
    adosc,
    cmf,
    efi,
    pvt,
    eom,
    nvi,
    pvi,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def finite(v):
    return v is not None and not math.isnan(v) and not math.isinf(v)


def flat_series(n, value=100.0):
    return [value] * n


# ---------------------------------------------------------------------------
# ad  (Chaikin A/D line)
# ---------------------------------------------------------------------------

class TestAd:
    def test_length(self):
        n = 20
        highs  = [101.0] * n
        lows   = [99.0] * n
        closes = [101.0] * n   # close at high
        volumes = [1000.0] * n
        out = ad(highs, lows, closes, volumes)
        assert len(out) == n

    def test_rises_when_closes_near_highs(self):
        # CLV = ((close-low)-(high-close))/(high-low) = 1 when close == high
        # So cumulative sum increases each bar
        n = 20
        highs   = [101.0] * n
        lows    = [99.0] * n
        closes  = [101.0] * n   # close == high → CLV = 1.0
        volumes = [1000.0] * n
        out = ad(highs, lows, closes, volumes)
        # Each step should add +1000
        for i in range(1, n):
            assert out[i] > out[i - 1]

    def test_falls_when_closes_near_lows(self):
        n = 20
        highs   = [101.0] * n
        lows    = [99.0] * n
        closes  = [99.0] * n   # close == low → CLV = -1.0
        volumes = [1000.0] * n
        out = ad(highs, lows, closes, volumes)
        for i in range(1, n):
            assert out[i] < out[i - 1]

    def test_known_first_bar(self):
        # CLV = ((101-99)-(101-101))/(101-99) = 2/2 = 1.0; ad[0] = 1.0 * 500 = 500
        out = ad([101.0], [99.0], [101.0], [500.0])
        assert out[0] == pytest.approx(500.0)

    def test_zero_range_bar(self):
        # h == l → CLV = 0
        out = ad([100.0], [100.0], [100.0], [1000.0])
        assert out[0] == pytest.approx(0.0)

    def test_no_none_values(self):
        n = 10
        highs   = [101.0] * n
        lows    = [99.0] * n
        closes  = [100.5] * n
        volumes = [1000.0] * n
        out = ad(highs, lows, closes, volumes)
        assert all(v is not None for v in out)


# ---------------------------------------------------------------------------
# adosc  (Chaikin A/D Oscillator)
# ---------------------------------------------------------------------------

class TestAdosc:
    def test_length(self):
        n = 30
        highs   = [101.0] * n
        lows    = [99.0] * n
        closes  = [100.5] * n
        volumes = [1000.0] * n
        out = adosc(highs, lows, closes, volumes, fast=3, slow=10)
        assert len(out) == n

    def test_finite_tail(self):
        n = 50
        highs   = [100.0 + i * 0.1 for i in range(n)]
        lows    = [99.0 + i * 0.1 for i in range(n)]
        closes  = [99.5 + i * 0.1 for i in range(n)]
        volumes = [1000.0] * n
        out = adosc(highs, lows, closes, volumes, fast=3, slow=10)
        tail = [v for v in out[-5:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)

    def test_flat_ad_zero_adosc(self):
        # When AD is constant (close in middle), the two EMAs should converge to the same value → adosc → 0
        # close exactly at midpoint of [low, high] → CLV = 0 → AD never changes → EMA(AD) constant
        n = 40
        highs   = [102.0] * n
        lows    = [98.0] * n
        closes  = [100.0] * n   # midpoint → CLV = 0
        volumes = [1000.0] * n
        out = adosc(highs, lows, closes, volumes, fast=3, slow=10)
        post = [v for v in out if v is not None]
        assert all(abs(v) < 1e-9 for v in post)


# ---------------------------------------------------------------------------
# cmf  (Chaikin Money Flow)
# ---------------------------------------------------------------------------

class TestCmf:
    def test_length(self):
        n = 30
        out = cmf([101.0] * n, [99.0] * n, [100.5] * n, [1000.0] * n, 20)
        assert len(out) == n

    def test_in_minus1_1(self):
        n = 50
        highs   = [100.0 + i * 0.2 for i in range(n)]
        lows    = [99.0 + i * 0.2 for i in range(n)]
        closes  = [99.5 + i * 0.2 for i in range(n)]
        volumes = [1000.0 + i * 10 for i in range(n)]
        out = cmf(highs, lows, closes, volumes, 20)
        for v in out:
            if v is not None:
                assert -1.0 <= v <= 1.0, f"cmf={v} out of range"

    def test_near_1_when_close_at_high(self):
        n = 40
        highs   = [101.0] * n
        lows    = [99.0] * n
        closes  = [101.0] * n   # CLV = 1.0 → CMF = 1.0
        volumes = [1000.0] * n
        out = cmf(highs, lows, closes, volumes, 20)
        post = [v for v in out if v is not None]
        assert post
        assert all(v == pytest.approx(1.0) for v in post)

    def test_near_minus1_when_close_at_low(self):
        n = 40
        highs   = [101.0] * n
        lows    = [99.0] * n
        closes  = [99.0] * n   # CLV = -1.0 → CMF = -1.0
        volumes = [1000.0] * n
        out = cmf(highs, lows, closes, volumes, 20)
        post = [v for v in out if v is not None]
        assert post
        assert all(v == pytest.approx(-1.0) for v in post)


# ---------------------------------------------------------------------------
# efi  (Elder Force Index)
# ---------------------------------------------------------------------------

class TestEfi:
    def test_length_aligned(self):
        n = 30
        closes  = [100.0 + i for i in range(n)]
        volumes = [1000.0] * n
        out = efi(closes, volumes, 13)
        assert len(out) == n

    def test_finite_tail(self):
        n = 40
        closes  = [100.0 + i * 0.5 for i in range(n)]
        volumes = [1000.0 + i * 10 for i in range(n)]
        out = efi(closes, volumes, 13)
        tail = [v for v in out[-5:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)

    def test_positive_when_rising_and_constant_volume(self):
        # Rising prices with constant volume → force always positive → EFI should be positive
        n = 40
        closes  = [100.0 + i for i in range(n)]
        volumes = [1000.0] * n
        out = efi(closes, volumes, 5)
        post = [v for v in out if v is not None]
        assert all(v > 0 for v in post)


# ---------------------------------------------------------------------------
# pvt  (Price Volume Trend)
# ---------------------------------------------------------------------------

class TestPvt:
    def test_length(self):
        n = 20
        closes  = [100.0, 101.0, 99.0, 102.0, 100.5] * 4
        volumes = [1000.0] * n
        out = pvt(closes, volumes)
        assert len(out) == n

    def test_no_none_values(self):
        closes  = [10.0, 11.0, 12.0, 10.0]
        volumes = [100.0, 200.0, 150.0, 120.0]
        out = pvt(closes, volumes)
        assert all(v is not None for v in out)

    def test_known_recursion(self):
        # pvt[0] = 0
        # pvt[1] = pvt[0] + vol[1] * (close[1]-close[0])/close[0]
        #        = 0 + 200 * (11-10)/10 = 20.0
        # pvt[2] = 20 + 150 * (12-11)/11 ≈ 20 + 13.636 = 33.636
        closes  = [10.0, 11.0, 12.0]
        volumes = [100.0, 200.0, 150.0]
        out = pvt(closes, volumes)
        assert out[0] == pytest.approx(0.0)
        assert out[1] == pytest.approx(20.0)
        assert out[2] == pytest.approx(20.0 + 150.0 * 1.0 / 11.0)


# ---------------------------------------------------------------------------
# eom  (Ease of Movement)
# ---------------------------------------------------------------------------

class TestEom:
    def test_length(self):
        n = 30
        highs   = [101.0 + i * 0.1 for i in range(n)]
        lows    = [99.0 + i * 0.1 for i in range(n)]
        volumes = [1000.0] * n
        out = eom(highs, lows, volumes, 14)
        assert len(out) == n

    def test_finite_tail(self):
        n = 40
        highs   = [101.0 + i * 0.1 for i in range(n)]
        lows    = [99.0 + i * 0.1 for i in range(n)]
        volumes = [1000.0] * n
        out = eom(highs, lows, volumes, 14)
        tail = [v for v in out[-5:] if v is not None]
        assert tail
        assert all(finite(v) for v in tail)


# ---------------------------------------------------------------------------
# nvi  (Negative Volume Index)
# ---------------------------------------------------------------------------

class TestNvi:
    def test_length(self):
        closes  = [10.0, 11.0, 12.0, 11.5, 10.5, 11.0]
        volumes = [100.0, 80.0, 90.0, 70.0, 110.0, 60.0]
        out = nvi(closes, volumes)
        assert len(out) == len(closes)

    def test_starts_at_1000(self):
        closes  = [10.0, 11.0, 12.0]
        volumes = [100.0, 80.0, 90.0]
        out = nvi(closes, volumes)
        assert out[0] == pytest.approx(1000.0)

    def test_known_recursion(self):
        # volumes[1]=80 < volumes[0]=100 → nvi[1] = nvi[0]*(1+rocp) = 1000*(1+(11-10)/10) = 1100
        # volumes[2]=90 > volumes[1]=80 → nvi[2] = nvi[1] = 1100 (unchanged)
        closes  = [10.0, 11.0, 12.0]
        volumes = [100.0, 80.0, 90.0]
        out = nvi(closes, volumes)
        assert out[0] == pytest.approx(1000.0)
        assert out[1] == pytest.approx(1100.0)
        assert out[2] == pytest.approx(1100.0)

    def test_unchanged_on_rising_volume(self):
        # volume keeps rising → nvi never changes
        closes  = [10.0, 11.0, 12.0, 13.0]
        volumes = [100.0, 110.0, 120.0, 130.0]
        out = nvi(closes, volumes)
        assert all(v == pytest.approx(1000.0) for v in out)

    def test_no_none_values(self):
        closes  = [10.0, 11.0, 9.0, 10.5]
        volumes = [100.0, 80.0, 70.0, 120.0]
        out = nvi(closes, volumes)
        assert all(v is not None for v in out)


# ---------------------------------------------------------------------------
# pvi  (Positive Volume Index)
# ---------------------------------------------------------------------------

class TestPvi:
    def test_length(self):
        closes  = [10.0, 11.0, 12.0, 11.5, 10.5, 11.0]
        volumes = [100.0, 120.0, 110.0, 130.0, 90.0, 140.0]
        out = pvi(closes, volumes)
        assert len(out) == len(closes)

    def test_starts_at_1000(self):
        closes  = [10.0, 11.0, 12.0]
        volumes = [100.0, 120.0, 110.0]
        out = pvi(closes, volumes)
        assert out[0] == pytest.approx(1000.0)

    def test_known_recursion(self):
        # volumes[1]=120 > volumes[0]=100 → pvi[1] = 1000*(1+(11-10)/10) = 1100
        # volumes[2]=110 < volumes[1]=120 → pvi[2] = pvi[1] = 1100 (unchanged)
        closes  = [10.0, 11.0, 12.0]
        volumes = [100.0, 120.0, 110.0]
        out = pvi(closes, volumes)
        assert out[0] == pytest.approx(1000.0)
        assert out[1] == pytest.approx(1100.0)
        assert out[2] == pytest.approx(1100.0)

    def test_unchanged_on_falling_volume(self):
        # volume keeps falling → pvi never changes
        closes  = [10.0, 11.0, 12.0, 13.0]
        volumes = [100.0, 90.0, 80.0, 70.0]
        out = pvi(closes, volumes)
        assert all(v == pytest.approx(1000.0) for v in out)

    def test_no_none_values(self):
        closes  = [10.0, 11.0, 9.0, 10.5]
        volumes = [100.0, 120.0, 110.0, 130.0]
        out = pvi(closes, volumes)
        assert all(v is not None for v in out)
