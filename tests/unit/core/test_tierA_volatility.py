"""Correctness tests for Tier A volatility indicators (Task 4):
natr, stddev, hvol, bbands_pctb, bbands_width, donchian_width, ulcer, chop, mass.
"""

import math

import pytest

from vike_trader_app.core.indicators.volatility import (
    atr,
    bollinger,
    donchian,
    natr,
    stddev,
    hvol,
    bbands_pctb,
    bbands_width,
    donchian_width,
    ulcer,
    chop,
    mass,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _closes(n=60, base=100.0, amp=5.0):
    return [base + amp * math.sin(i / 5.0) for i in range(n)]


def _ohlcv(n=60, base=100.0, amp=5.0):
    closes = _closes(n, base, amp)
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    return highs, lows, closes


def _tail(lst, n=10):
    return [v for v in lst[-n:] if v is not None]


# ── natr ─────────────────────────────────────────────────────────────────────

class TestNatr:
    def test_length(self):
        h, l, c = _ohlcv()
        assert len(natr(h, l, c, 14)) == 60

    def test_warmup_none(self):
        h, l, c = _ohlcv()
        assert natr(h, l, c, 14)[0] is None

    def test_equals_100_atr_over_close(self):
        """natr[i] == 100 * atr[i] / close[i] pointwise (post-warmup)."""
        h, l, c = _ohlcv(80)
        period = 14
        natr_out = natr(h, l, c, period)
        atr_out = atr(h, l, c, period)
        for i in range(len(c)):
            if natr_out[i] is not None:
                expected = 100.0 * atr_out[i] / c[i]
                assert natr_out[i] == pytest.approx(expected, rel=1e-9)

    def test_positive(self):
        h, l, c = _ohlcv(80)
        tail = _tail(natr(h, l, c, 14))
        assert tail and all(v > 0 for v in tail)


# ── stddev ────────────────────────────────────────────────────────────────────

class TestStddev:
    def test_length(self):
        assert len(stddev([1.0] * 50, 20)) == 50

    def test_warmup_none(self):
        assert stddev([1.0] * 50, 20)[0] is None

    def test_constant_is_zero(self):
        out = stddev([5.0] * 60, 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(0.0, abs=1e-12) for v in tail)

    def test_known_window(self):
        """Population stddev of [1,2,3,4,5]: mean=3, var=2, std=sqrt(2)."""
        # build a series where the last window is exactly [1,2,3,4,5]
        vals = [3.0] * 20 + [1.0, 2.0, 3.0, 4.0, 5.0]
        out = stddev(vals, 5)
        assert out[-1] == pytest.approx(math.sqrt(2.0), rel=1e-9)

    def test_output_nonnegative(self):
        closes = _closes(80)
        out = stddev(closes, 20)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)


# ── hvol ─────────────────────────────────────────────────────────────────────

class TestHvol:
    def test_length(self):
        assert len(hvol([100.0 + i * 0.1 for i in range(80)], 20)) == 80

    def test_warmup_none(self):
        vals = [100.0 + i * 0.1 for i in range(80)]
        assert hvol(vals, 20)[0] is None

    def test_positive_on_trending(self):
        vals = [100.0 + i * 0.5 for i in range(80)]
        out = hvol(vals, 20)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)

    def test_flat_is_zero(self):
        """Constant price → zero log returns → hvol = 0."""
        out = hvol([100.0] * 60, 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(0.0, abs=1e-9) for v in tail)


# ── bbands_pctb ───────────────────────────────────────────────────────────────

class TestBbandsPctb:
    def test_length(self):
        assert len(bbands_pctb([100.0] * 50, 20, 2.0)) == 50

    def test_warmup_none(self):
        assert bbands_pctb([100.0] * 50, 20, 2.0)[0] is None

    def test_constant_series_is_nan_or_half(self):
        """For a flat series stddev=0 so upper==lower: result may be NaN or 0.5."""
        out = bbands_pctb([5.0] * 60, 20, 2.0)
        for v in _tail(out):
            assert v is None or math.isnan(v) or v == pytest.approx(0.5)

    def test_matches_bollinger_formula(self):
        """pctb[i] == (close[i] - lower[i]) / (upper[i] - lower[i])."""
        vals = _closes(80)
        period, k = 20, 2.0
        pctb = bbands_pctb(vals, period, k)
        upper, mid, lower = bollinger(vals, period, k)
        for i in range(len(vals)):
            if pctb[i] is not None and upper[i] is not None:
                bw = upper[i] - lower[i]
                if abs(bw) > 1e-12:
                    expected = (vals[i] - lower[i]) / bw
                    assert pctb[i] == pytest.approx(expected, rel=1e-9)

    def test_output_finite(self):
        vals = _closes(80)
        out = bbands_pctb(vals, 20, 2.0)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)


# ── bbands_width ─────────────────────────────────────────────────────────────

class TestBbandsWidth:
    def test_length(self):
        assert len(bbands_width([100.0] * 50, 20, 2.0)) == 50

    def test_warmup_none(self):
        assert bbands_width([100.0] * 50, 20, 2.0)[0] is None

    def test_nonnegative(self):
        vals = _closes(80)
        out = bbands_width(vals, 20, 2.0)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)

    def test_matches_bollinger_formula(self):
        """width[i] == (upper[i] - lower[i]) / mid[i]."""
        vals = _closes(80)
        period, k = 20, 2.0
        width = bbands_width(vals, period, k)
        upper, mid, lower = bollinger(vals, period, k)
        for i in range(len(vals)):
            if width[i] is not None and mid[i] is not None and abs(mid[i]) > 1e-12:
                expected = (upper[i] - lower[i]) / mid[i]
                assert width[i] == pytest.approx(expected, rel=1e-9)


# ── donchian_width ────────────────────────────────────────────────────────────

class TestDonchianWidth:
    def test_length(self):
        n = 60
        h = [110.0] * n
        l = [90.0] * n
        assert len(donchian_width(h, l, 20)) == n

    def test_warmup_none(self):
        n = 60
        h = [110.0] * n
        l = [90.0] * n
        assert donchian_width(h, l, 20)[0] is None

    def test_nonnegative(self):
        h, l, _ = _ohlcv(80)
        out = donchian_width(h, l, 20)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)

    def test_matches_donchian_formula(self):
        """width[i] == donchian_upper[i] - donchian_lower[i]."""
        h, l, _ = _ohlcv(80)
        period = 20
        width = donchian_width(h, l, period)
        upper, mid, lower = donchian(h, l, period)
        for i in range(len(h)):
            if width[i] is not None and upper[i] is not None:
                assert width[i] == pytest.approx(upper[i] - lower[i], rel=1e-9)

    def test_constant_bands_gives_correct_width(self):
        n = 40
        highs = [110.0] * n
        lows = [90.0] * n
        out = donchian_width(highs, lows, 20)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(20.0) for v in tail)


# ── ulcer ─────────────────────────────────────────────────────────────────────

class TestUlcer:
    def test_length(self):
        assert len(ulcer([100.0] * 50, 14)) == 50

    def test_warmup_none(self):
        assert ulcer([100.0] * 50, 14)[0] is None

    def test_nonnegative(self):
        out = ulcer(_closes(80), 14)
        tail = _tail(out)
        assert tail and all(v >= 0 for v in tail)

    def test_flat_series_is_zero(self):
        """No drawdown from a flat series → ulcer = 0."""
        out = ulcer([100.0] * 60, 14)
        tail = _tail(out)
        assert tail and all(v == pytest.approx(0.0, abs=1e-12) for v in tail)

    def test_declining_series_positive(self):
        """A monotonically declining series should have positive ulcer."""
        vals = [100.0 - i * 0.5 for i in range(60)]
        out = ulcer(vals, 14)
        tail = _tail(out)
        assert tail and all(v > 0 for v in tail)


# ── chop ──────────────────────────────────────────────────────────────────────

class TestChop:
    def test_length(self):
        h, l, c = _ohlcv()
        assert len(chop(h, l, c, 14)) == 60

    def test_warmup_none(self):
        h, l, c = _ohlcv()
        assert chop(h, l, c, 14)[0] is None

    def test_finite_on_real_data(self):
        h, l, c = _ohlcv(80)
        out = chop(h, l, c, 14)
        tail = _tail(out)
        assert tail and all(math.isfinite(v) for v in tail)

    def test_bounded_between_0_and_100(self):
        """CHOP oscillates between 0 and 100 by construction."""
        h, l, c = _ohlcv(80)
        out = chop(h, l, c, 14)
        tail = _tail(out)
        assert tail and all(0 < v < 100 for v in tail)


# ── mass ──────────────────────────────────────────────────────────────────────

class TestMass:
    def test_length(self):
        h, l, c = _ohlcv()
        assert len(mass(h, l, 25, 9)) == 60

    def test_warmup_none(self):
        h, l, c = _ohlcv()
        assert mass(h, l, 25, 9)[0] is None

    def test_positive(self):
        h, l, _ = _ohlcv(80)
        out = mass(h, l, 25, 9)
        tail = _tail(out)
        assert tail and all(v > 0 for v in tail)

    def test_output_length_aligned(self):
        h, l, _ = _ohlcv(100)
        out = mass(h, l, 25, 9)
        assert len(out) == 100


# ── multi-output alignment ────────────────────────────────────────────────────

class TestMultiOutputAlignment:
    def test_bbands_pctb_and_width_same_length_as_bollinger(self):
        vals = _closes(80)
        period, k = 20, 2.0
        pctb = bbands_pctb(vals, period, k)
        width = bbands_width(vals, period, k)
        upper, mid, lower = bollinger(vals, period, k)
        assert len(pctb) == len(upper) == len(width) == 80

    def test_natr_same_length_as_atr(self):
        h, l, c = _ohlcv(80)
        assert len(natr(h, l, c, 14)) == len(atr(h, l, c, 14)) == 80

    def test_donchian_width_same_length_as_donchian_upper(self):
        h, l, _ = _ohlcv(80)
        dw = donchian_width(h, l, 20)
        upper, mid, lower = donchian(h, l, 20)
        assert len(dw) == len(upper) == 80


# ── registry ──────────────────────────────────────────────────────────────────

class TestRegistration:
    def test_new_volatility_indicators_registered(self):
        from vike_trader_app.core.indicators import base
        names = {s.name for s in base.list_indicators(category="volatility")}
        expected = {"natr", "stddev", "hvol", "bbands_pctb", "bbands_width",
                    "donchian_width", "ulcer", "chop", "mass"}
        missing = expected - names
        assert not missing, f"Not registered: {missing}"
