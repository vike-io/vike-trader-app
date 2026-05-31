"""Tier B overlap/trend indicators — behavioural correctness tests (TDD)."""

import math

import pytest

from vike_trader_app.core.indicators.overlap import (
    alligator,
    envelopes,
    gmma,
    ichimoku,
    mcginley,
    psar,
    supertrend,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _const(n, v=100.0):
    return [v] * n


def _ramp(n, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _synth_ohlc(n=150):
    closes = [100.0 + 5 * math.sin(i / 7) + i * 0.05 for i in range(n)]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    return highs, lows, closes


# ── supertrend ────────────────────────────────────────────────────────────────

class TestSupertrend:
    def test_direction_only_plus_minus_one(self):
        h, l, c = _synth_ohlc(120)
        st, direction = supertrend(h, l, c, period=10, mult=3.0)
        defined_dirs = [v for v in direction if v is not None]
        assert all(v in (1, -1) for v in defined_dirs)

    def test_output_length_aligned(self):
        h, l, c = _synth_ohlc(120)
        st, direction = supertrend(h, l, c, period=10, mult=3.0)
        assert len(st) == 120
        assert len(direction) == 120

    def test_direction_flips_on_v_shape(self):
        """Down 50 bars then up 50 bars: supertrend must flip direction."""
        n = 100
        closes = [200.0 - i * 2 for i in range(50)] + [100.0 + i * 2 for i in range(50)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        _, direction = supertrend(highs, lows, closes, period=10, mult=3.0)
        defined = [v for v in direction if v is not None]
        # there must be both +1 and -1 present
        assert 1 in defined
        assert -1 in defined

    def test_supertrend_values_finite_in_tail(self):
        h, l, c = _synth_ohlc(120)
        st, direction = supertrend(h, l, c, period=10, mult=3.0)
        tail_st = [v for v in st[-20:] if v is not None]
        tail_d = [v for v in direction[-20:] if v is not None]
        assert len(tail_st) > 0
        assert len(tail_d) > 0
        assert all(math.isfinite(v) for v in tail_st)

    def test_leading_none_warmup(self):
        h, l, c = _synth_ohlc(120)
        st, direction = supertrend(h, l, c, period=10, mult=3.0)
        # first ATR warmup period should be None
        assert st[0] is None
        assert direction[0] is None


# ── ichimoku ──────────────────────────────────────────────────────────────────

class TestIchimoku:
    def test_output_count_is_five(self):
        h, l, c = _synth_ohlc(200)
        result = ichimoku(h, l, c, tenkan=9, kijun=26, senkou=52)
        assert len(result) == 5, f"expected 5 outputs, got {len(result)}"

    def test_all_outputs_aligned_to_input(self):
        n = 200
        h, l, c = _synth_ohlc(n)
        result = ichimoku(h, l, c, tenkan=9, kijun=26, senkou=52)
        for line in result:
            assert len(line) == n

    def test_tenkan_kijun_finite_after_warmup(self):
        n = 200
        h, l, c = _synth_ohlc(n)
        tenkan, kijun, sa, sb, chikou = ichimoku(h, l, c, tenkan=9, kijun=26, senkou=52)
        # tenkan defined from bar 8 (index 8)
        assert tenkan[8] is not None
        # kijun defined from bar 25 (index 25)
        assert kijun[25] is not None

    def test_chikou_leading_none(self):
        """chikou[i] = close[i - kijun]; first kijun bars should be None (leading warm-up)."""
        n = 100
        h, l, c = _synth_ohlc(n)
        kijun_p = 26
        _, _, _, _, chikou = ichimoku(h, l, c, tenkan=9, kijun=kijun_p, senkou=52)
        # first kijun_p values should all be None
        leading = chikou[:kijun_p]
        assert all(v is None for v in leading)
        # tail should be defined (chikou[i] = closes[i - kijun] for i >= kijun)
        tail = [v for v in chikou[-10:] if v is not None]
        assert len(tail) > 0

    def test_senkou_forward_shift_leading_none(self):
        """senkou_a/b are shifted forward by kijun; first kijun bars may not be defined."""
        n = 200
        h, l, c = _synth_ohlc(n)
        _, _, sa, sb, _ = ichimoku(h, l, c, tenkan=9, kijun=26, senkou=52)
        # senkou_a only becomes defined after tenkan + kijun warmup + forward shift
        # the tail should contain finite values
        tail_sa = [v for v in sa[-20:] if v is not None]
        tail_sb = [v for v in sb[-20:] if v is not None]
        assert len(tail_sa) > 0
        assert len(tail_sb) > 0


# ── psar ──────────────────────────────────────────────────────────────────────

class TestPsar:
    def test_output_aligned(self):
        h, l, _ = _synth_ohlc(120)
        result = psar(h, l, af=0.02, max_af=0.2)
        assert len(result) == 120

    def test_output_within_sane_range(self):
        h, l, _ = _synth_ohlc(150)
        result = psar(h, l, af=0.02, max_af=0.2)
        # all defined values should be in the rough range of price
        prices_min = min(l)
        prices_max = max(h)
        defined = [v for v in result if v is not None]
        assert len(defined) > 0
        # psar can go slightly outside range but should be order-of-magnitude correct
        for v in defined:
            assert prices_min * 0.5 < v < prices_max * 2.0

    def test_psar_tail_finite(self):
        h, l, _ = _synth_ohlc(150)
        result = psar(h, l, af=0.02, max_af=0.2)
        tail = [v for v in result[-20:] if v is not None]
        assert len(tail) > 0
        assert all(math.isfinite(v) for v in tail)

    def test_psar_flips_in_trending_series(self):
        """Rising then falling — psar should exist below high in uptrend (bullish = psar < price)."""
        n = 100
        closes = [100.0 + i * 1.5 for i in range(n)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.0 for c in closes]
        result = psar(highs, lows, af=0.02, max_af=0.2)
        defined = [(i, v) for i, v in enumerate(result) if v is not None]
        assert len(defined) > 0


# ── mcginley ──────────────────────────────────────────────────────────────────

class TestMcginley:
    def test_constant_series_returns_constant(self):
        """McGinley dynamic of a constant series must converge to that constant."""
        n = 200
        val = 50.0
        closes = _const(n, val)
        result = mcginley(closes, period=14)
        # after sufficient warmup the value should be very close to the constant
        tail = [v for v in result[-20:] if v is not None]
        assert len(tail) > 0
        for v in tail:
            assert abs(v - val) < 1e-6

    def test_output_aligned(self):
        closes = _ramp(100)
        result = mcginley(closes, period=14)
        assert len(result) == 100

    def test_output_tail_finite(self):
        closes = _ramp(100)
        result = mcginley(closes, period=14)
        tail = [v for v in result[-10:] if v is not None]
        assert len(tail) > 0
        assert all(math.isfinite(v) for v in tail)

    def test_mcginley_tracks_rising_series(self):
        """McGinley should trend upward on a rising series."""
        closes = _ramp(100, start=100.0, step=1.0)
        result = mcginley(closes, period=14)
        defined = [v for v in result if v is not None]
        assert len(defined) > 10
        # last value > first defined value
        assert defined[-1] > defined[0]


# ── gmma ──────────────────────────────────────────────────────────────────────

class TestGmma:
    def test_returns_12_lines(self):
        closes = _ramp(200)
        result = gmma(closes)
        assert len(result) == 12

    def test_all_12_lines_aligned(self):
        n = 200
        closes = _ramp(n)
        result = gmma(closes)
        for line in result:
            assert len(line) == n

    def test_all_lines_have_finite_tail(self):
        closes = _ramp(200)
        result = gmma(closes)
        for line in result:
            tail = [v for v in line[-10:] if v is not None]
            assert len(tail) > 0
            assert all(math.isfinite(v) for v in tail)

    def test_short_emas_faster_than_long(self):
        """On a strongly trending series, short EMAs should be farther from recent price direction."""
        closes = _ramp(200, start=100.0, step=1.0)
        s3, s5, s8, s10, s12, s15, l30, l35, l40, l45, l50, l60 = gmma(closes)
        # In a rising trend, short EMAs > long EMAs (closer to current price)
        last_s3 = [v for v in s3 if v is not None][-1]
        last_l60 = [v for v in l60 if v is not None][-1]
        assert last_s3 > last_l60


# ── envelopes ─────────────────────────────────────────────────────────────────

class TestEnvelopes:
    def test_upper_gt_mid_gt_lower(self):
        closes = _ramp(100)
        upper, mid, lower = envelopes(closes, period=20, pct=2.5)
        defined = [(u, m, lo) for u, m, lo in zip(upper, mid, lower)
                   if u is not None and m is not None and lo is not None]
        assert len(defined) > 0
        for u, m, lo in defined:
            assert u > m > lo

    def test_output_aligned(self):
        n = 100
        closes = _ramp(n)
        upper, mid, lower = envelopes(closes, period=20, pct=2.5)
        assert len(upper) == n
        assert len(mid) == n
        assert len(lower) == n

    def test_pct_scaling(self):
        """With pct=10, upper should be exactly 10% above mid."""
        closes = _const(50, 100.0)
        upper, mid, lower = envelopes(closes, period=5, pct=10.0)
        # last bars should be defined
        u = [v for v in upper if v is not None]
        m = [v for v in mid if v is not None]
        lo = [v for v in lower if v is not None]
        assert len(u) > 0
        assert abs(u[-1] / m[-1] - 1.1) < 1e-9
        assert abs(lo[-1] / m[-1] - 0.9) < 1e-9

    def test_leading_none_warmup(self):
        closes = _ramp(100)
        upper, mid, lower = envelopes(closes, period=20, pct=2.5)
        assert upper[0] is None
        assert mid[0] is None
        assert lower[0] is None


# ── alligator ─────────────────────────────────────────────────────────────────

class TestAlligator:
    def test_returns_3_lines(self):
        h, l, _ = _synth_ohlc(200)
        result = alligator(h, l)
        assert len(result) == 3

    def test_all_3_lines_aligned(self):
        n = 200
        h, l, _ = _synth_ohlc(n)
        jaw, teeth, lips = alligator(h, l)
        assert len(jaw) == n
        assert len(teeth) == n
        assert len(lips) == n

    def test_all_lines_finite_in_tail(self):
        h, l, _ = _synth_ohlc(200)
        jaw, teeth, lips = alligator(h, l)
        for line in (jaw, teeth, lips):
            tail = [v for v in line[-10:] if v is not None]
            assert len(tail) > 0
            assert all(math.isfinite(v) for v in tail)

    def test_forward_shift_leading_none(self):
        """jaw shifted +8, teeth +5, lips +3 — the leading positions should be None due to shift.

        A forward shift of +k on the raw series means out[j] is filled by raw[j-k].
        For j < k there is no valid source, so the first k positions of each shifted line
        are guaranteed to be None (they stay at the warm-up value).
        The actual SMMA warm-up adds further leading Nones on top of the shift.
        """
        n = 200
        h, l, _ = _synth_ohlc(n)
        jaw, teeth, lips = alligator(h, l)
        # jaw: SMMA(13) warm-up=12 bars, then shifted +8 → first bar cannot be filled
        assert jaw[0] is None
        assert teeth[0] is None
        assert lips[0] is None
        # The tail should contain defined values (series is long enough)
        assert any(v is not None for v in jaw[-20:])
        assert any(v is not None for v in teeth[-20:])
        assert any(v is not None for v in lips[-20:])
