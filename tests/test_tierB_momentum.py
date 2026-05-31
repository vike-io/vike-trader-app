"""Tier B momentum indicators — behavioural correctness tests (TDD, Task 2)."""

import math

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _ramp(n, start=10.0, step=1.0):
    return [start + i * step for i in range(n)]


def _const(n, v=100.0):
    return [v] * n


def finite(v):
    return v is not None and not math.isnan(v) and not math.isinf(v)


def _synth_ohlcv(n=150):
    closes = [100.0 + 10 * math.sin(i / 7) + (i % 5) for i in range(n)]
    opens  = [c - 0.3 for c in closes]
    highs  = [c + 1.5 for c in closes]
    lows   = [c - 1.5 for c in closes]
    return opens, highs, lows, closes


# ── ao ───────────────────────────────────────────────────────────────────────

class TestAo:
    def test_positive_on_rising_series(self):
        from vike_trader_app.core.indicators.momentum import ao
        # Strictly rising highs/lows → SMA5 of median > SMA34 of median eventually → ao > 0
        n = 120
        highs = _ramp(n, start=101.0, step=1.0)
        lows  = _ramp(n, start=99.0, step=1.0)
        out   = ao(highs, lows)
        post  = [v for v in out if v is not None]
        assert post, "ao must have defined values on 120-bar series"
        assert all(v > 0 for v in post), "ao > 0 expected for a rising series"

    def test_length_aligned(self):
        from vike_trader_app.core.indicators.momentum import ao
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        out = ao(h, l)
        assert len(out) == n

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import ao
        h = _ramp(80, 101.0)
        l = _ramp(80, 99.0)
        out = ao(h, l)
        tail = [v for v in out[-10:] if v is not None]
        assert tail and all(finite(v) for v in tail)


# ── ac ───────────────────────────────────────────────────────────────────────

class TestAc:
    def test_length_aligned(self):
        from vike_trader_app.core.indicators.momentum import ac
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        out = ac(h, l)
        assert len(out) == n

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import ac
        h = _ramp(80, 101.0)
        l = _ramp(80, 99.0)
        out = ac(h, l)
        tail = [v for v in out[-10:] if v is not None]
        assert tail and all(finite(v) for v in tail)

    def test_warmup_nones(self):
        from vike_trader_app.core.indicators.momentum import ac
        # ac needs ao (SMA34) + SMA5 of ao → warm-up >= 34 + 5 - 1 = 38 bars
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        out = ac(h, l)
        assert out[0] is None, "first bar must be None (warm-up)"


# ── fisher ───────────────────────────────────────────────────────────────────

class TestFisher:
    def test_output_count_and_length(self):
        from vike_trader_app.core.indicators.momentum import fisher
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        result = fisher(h, l)
        assert isinstance(result, tuple) and len(result) == 2
        fish, trigger = result
        assert len(fish) == n
        assert len(trigger) == n

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import fisher
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        fish, trigger = fisher(h, l)
        tail_f = [v for v in fish[-10:] if v is not None]
        tail_t = [v for v in trigger[-10:] if v is not None]
        assert tail_f and all(finite(v) for v in tail_f)
        assert tail_t and all(finite(v) for v in tail_t)

    def test_trigger_lags_fisher(self):
        from vike_trader_app.core.indicators.momentum import fisher
        _, lows = _const(80, 99.0), _const(80, 99.0)
        h = [101.0 + math.sin(i / 5) for i in range(80)]
        l = [99.0 + math.sin(i / 5) for i in range(80)]
        fish, trigger = fisher(h, l)
        # Where both are defined, trigger at i == fisher at i-1
        defined_pairs = [(i, fish[i], trigger[i]) for i in range(1, 80)
                         if fish[i - 1] is not None and trigger[i] is not None]
        assert defined_pairs, "should have overlapping defined pairs"
        for i, f, t in defined_pairs:
            assert t == pytest.approx(fish[i - 1], abs=1e-9)


# ── connors_rsi ───────────────────────────────────────────────────────────────

class TestConnorsRsi:
    def test_in_0_100(self):
        from vike_trader_app.core.indicators.momentum import connors_rsi
        values = [100 + 5 * math.sin(i / 4) + (i % 7) for i in range(120)]
        out = connors_rsi(values)
        post = [v for v in out if v is not None]
        assert post, "connors_rsi must produce defined values"
        assert all(0.0 - 1e-9 <= v <= 100.0 + 1e-9 for v in post), \
            f"crsi out of [0,100]: {[v for v in post if not (0 - 1e-9 <= v <= 100 + 1e-9)]}"

    def test_length_aligned(self):
        from vike_trader_app.core.indicators.momentum import connors_rsi
        values = _ramp(120)
        out = connors_rsi(values)
        assert len(out) == 120

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import connors_rsi
        values = [100 + 5 * math.sin(i / 4) for i in range(120)]
        out = connors_rsi(values)
        tail = [v for v in out[-10:] if v is not None]
        assert tail and all(finite(v) for v in tail)


# ── coppock ───────────────────────────────────────────────────────────────────

class TestCoppock:
    def test_finite(self):
        from vike_trader_app.core.indicators.momentum import coppock
        values = [100 + 3 * math.sin(i / 5) for i in range(100)]
        out = coppock(values)
        post = [v for v in out if v is not None]
        assert post, "coppock must produce defined values"
        assert all(finite(v) for v in post)

    def test_length_aligned(self):
        from vike_trader_app.core.indicators.momentum import coppock
        out = coppock(_ramp(100))
        assert len(out) == 100


# ── elder_ray ─────────────────────────────────────────────────────────────────

class TestElderRay:
    def test_output_count_and_length(self):
        from vike_trader_app.core.indicators.momentum import elder_ray
        n = 60
        highs  = _ramp(n, 101.0)
        lows   = _ramp(n, 99.0)
        closes = _ramp(n, 100.0)
        result = elder_ray(highs, lows, closes)
        assert isinstance(result, tuple) and len(result) == 2
        bull, bear = result
        assert len(bull) == n
        assert len(bear) == n

    def test_bull_positive_in_uptrend(self):
        from vike_trader_app.core.indicators.momentum import elder_ray
        n = 80
        closes = _ramp(n, start=100.0, step=2.0)   # strong up-trend
        highs  = [c + 1.0 for c in closes]
        lows   = [c - 1.0 for c in closes]
        bull, bear = elder_ray(highs, lows, closes, period=13)
        post_bull = [v for v in bull if v is not None]
        assert post_bull, "elder_ray must produce defined values"
        assert all(v > 0 for v in post_bull), "bull_power > 0 expected in uptrend"

    def test_bear_negative_in_downtrend(self):
        from vike_trader_app.core.indicators.momentum import elder_ray
        # In a downtrend EMA is above close → bear_power (low - EMA) < 0
        n = 80
        closes = [250.0 - i * 2.0 for i in range(n)]   # falling
        highs  = [c + 1.0 for c in closes]
        lows   = [c - 1.0 for c in closes]
        bull, bear = elder_ray(highs, lows, closes, period=13)
        post_bear = [v for v in bear if v is not None]
        assert post_bear, "elder_ray must produce defined bear_power values"
        # EMA lags above close in a downtrend → low - EMA < 0 in the tail
        tail_bear = post_bear[-20:]
        assert all(v < 0 for v in tail_bear), \
            f"bear_power tail should be < 0 in downtrend, got {tail_bear}"


# ── relative_vigor ────────────────────────────────────────────────────────────

class TestRelativeVigor:
    def test_output_count_and_length(self):
        from vike_trader_app.core.indicators.momentum import relative_vigor
        o, h, l, c = _synth_ohlcv(100)
        result = relative_vigor(o, h, l, c)
        assert isinstance(result, tuple) and len(result) == 2
        rvgi, sig = result
        assert len(rvgi) == 100
        assert len(sig) == 100

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import relative_vigor
        o, h, l, c = _synth_ohlcv(100)
        rvgi, sig = relative_vigor(o, h, l, c)
        tail_r = [v for v in rvgi[-10:] if v is not None]
        tail_s = [v for v in sig[-10:] if v is not None]
        assert tail_r and all(finite(v) for v in tail_r)
        assert tail_s and all(finite(v) for v in tail_s)

    def test_aligned(self):
        from vike_trader_app.core.indicators.momentum import relative_vigor
        n = 100
        o, h, l, c = _synth_ohlcv(n)
        rvgi, sig = relative_vigor(o, h, l, c)
        assert len(rvgi) == n and len(sig) == n


# ── smi_ergodic ───────────────────────────────────────────────────────────────

class TestSmiErgodic:
    def test_output_count_and_length(self):
        from vike_trader_app.core.indicators.momentum import smi_ergodic
        values = [100 + 5 * math.sin(i / 7) for i in range(100)]
        result = smi_ergodic(values)
        assert isinstance(result, tuple) and len(result) == 2
        smi, sig = result
        assert len(smi) == 100
        assert len(sig) == 100

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import smi_ergodic
        values = [100 + 5 * math.sin(i / 7) for i in range(100)]
        smi, sig = smi_ergodic(values)
        tail_s = [v for v in smi[-10:] if v is not None]
        tail_g = [v for v in sig[-10:] if v is not None]
        assert tail_s and all(finite(v) for v in tail_s)
        assert tail_g and all(finite(v) for v in tail_g)

    def test_aligned(self):
        from vike_trader_app.core.indicators.momentum import smi_ergodic
        values = [100 + 5 * math.sin(i / 7) for i in range(100)]
        smi, sig = smi_ergodic(values)
        assert len(smi) == 100 and len(sig) == 100


# ── vortex ────────────────────────────────────────────────────────────────────

class TestVortex:
    def test_vi_plus_and_minus_positive(self):
        from vike_trader_app.core.indicators.momentum import vortex
        n = 60
        highs  = _ramp(n, 101.0)
        lows   = _ramp(n, 99.0)
        closes = _ramp(n, 100.0)
        vi_plus, vi_minus = vortex(highs, lows, closes)
        post_plus  = [v for v in vi_plus if v is not None]
        post_minus = [v for v in vi_minus if v is not None]
        assert post_plus  and all(v > 0 for v in post_plus)
        assert post_minus and all(v > 0 for v in post_minus)

    def test_output_count_and_length(self):
        from vike_trader_app.core.indicators.momentum import vortex
        n = 60
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        c = _ramp(n, 100.0)
        result = vortex(h, l, c)
        assert isinstance(result, tuple) and len(result) == 2
        vi_p, vi_m = result
        assert len(vi_p) == n and len(vi_m) == n

    def test_aligned_defined(self):
        from vike_trader_app.core.indicators.momentum import vortex
        n = 60
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        c = _ramp(n, 100.0)
        vi_p, vi_m = vortex(h, l, c)
        # vi_plus and vi_minus should have the same None/defined pattern
        for p, m in zip(vi_p, vi_m):
            assert (p is None) == (m is None), "vi_plus and vi_minus must be co-defined"


# ── chande_kroll_stop ─────────────────────────────────────────────────────────

class TestChandeKrollStop:
    def test_output_count_and_length(self):
        from vike_trader_app.core.indicators.momentum import chande_kroll_stop
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        c = _ramp(n, 100.0)
        result = chande_kroll_stop(h, l, c)
        assert isinstance(result, tuple) and len(result) == 2
        ls, ss = result
        assert len(ls) == n and len(ss) == n

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import chande_kroll_stop
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        c = _ramp(n, 100.0)
        ls, ss = chande_kroll_stop(h, l, c)
        tail_l = [v for v in ls[-10:] if v is not None]
        tail_s = [v for v in ss[-10:] if v is not None]
        assert tail_l and all(finite(v) for v in tail_l)
        assert tail_s and all(finite(v) for v in tail_s)

    def test_aligned(self):
        from vike_trader_app.core.indicators.momentum import chande_kroll_stop
        n = 80
        h = _ramp(n, 101.0)
        l = _ramp(n, 99.0)
        c = _ramp(n, 100.0)
        ls, ss = chande_kroll_stop(h, l, c)
        assert len(ls) == n and len(ss) == n


# ── asi ───────────────────────────────────────────────────────────────────────

class TestAsi:
    def test_length_aligned(self):
        from vike_trader_app.core.indicators.momentum import asi
        o, h, l, c = _synth_ohlcv(100)
        out = asi(o, h, l, c)
        assert len(out) == 100

    def test_finite_tail(self):
        from vike_trader_app.core.indicators.momentum import asi
        o, h, l, c = _synth_ohlcv(100)
        out = asi(o, h, l, c)
        tail = [v for v in out[-10:] if v is not None]
        assert tail and all(finite(v) for v in tail)

    def test_warmup_first_bar_zero_or_none(self):
        from vike_trader_app.core.indicators.momentum import asi
        o, h, l, c = _synth_ohlcv(100)
        out = asi(o, h, l, c)
        # first bar has no previous bar → None or 0
        assert out[0] is None or out[0] == pytest.approx(0.0)

    def test_rising_series_generally_positive(self):
        from vike_trader_app.core.indicators.momentum import asi
        n = 80
        closes = _ramp(n, start=100.0, step=2.0)
        opens  = [c - 0.5 for c in closes]
        highs  = [c + 1.0 for c in closes]
        lows   = [c - 1.0 for c in closes]
        out = asi(opens, highs, lows, closes)
        post = [v for v in out if v is not None]
        assert post
        # cumulative SI on a rising series should trend upward (last > first defined)
        assert post[-1] > post[0], "ASI should trend positive on a rising series"
