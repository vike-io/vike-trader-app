"""Tier B volume + volatility indicators — behavioural correctness tests (TDD).

Covers: kvo, net_volume, volume_osc (volume.py)
        relative_volatility, high_low_52w (volatility.py)
"""

import math

import pytest

from vike_trader_app.core.indicators.volume import (
    kvo,
    net_volume,
    volume_osc,
)
from vike_trader_app.core.indicators.volatility import (
    relative_volatility,
    high_low_52w,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _const(n, v=100.0):
    return [v] * n


def _ramp(n, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _tail(lst, n=10):
    return [v for v in lst[-n:] if v is not None]


def _finite(v):
    return v is not None and not math.isnan(v) and not math.isinf(v)


def _synth_ohlcv(n=120):
    closes = [100.0 + 5 * math.sin(i / 7) + i * 0.05 for i in range(n)]
    highs = [c + 1.5 for c in closes]
    lows = [c - 1.5 for c in closes]
    vols = [1000.0 + (i % 9) * 10 for i in range(n)]
    return highs, lows, closes, vols


# ── net_volume ────────────────────────────────────────────────────────────────

class TestNetVolume:
    def test_length_aligned(self):
        closes = _ramp(30)
        vols = _const(30, 1000.0)
        out = net_volume(closes, vols)
        assert len(out) == 30

    def test_no_none_values(self):
        closes = _ramp(20)
        vols = _const(20, 500.0)
        out = net_volume(closes, vols)
        assert all(v is not None for v in out)

    def test_sign_matches_close_direction(self):
        """Positive when close rises, negative when close falls."""
        closes = [100.0, 101.0, 102.0, 101.0, 100.0, 103.0]
        vols = [1000.0] * 6
        out = net_volume(closes, vols)
        # i=0: no prev close → 0
        assert out[0] == pytest.approx(0.0)
        # i=1: close rose → +1000
        assert out[1] > 0
        # i=2: close rose → +1000
        assert out[2] > 0
        # i=3: close fell → -1000
        assert out[3] < 0
        # i=4: close fell → -1000
        assert out[4] < 0
        # i=5: close rose → +1000
        assert out[5] > 0

    def test_zero_on_unchanged_close(self):
        closes = [100.0, 100.0, 100.0]
        vols = [500.0, 500.0, 500.0]
        out = net_volume(closes, vols)
        assert out[1] == pytest.approx(0.0)
        assert out[2] == pytest.approx(0.0)

    def test_not_cumulative(self):
        """net_volume is NOT cumulative — each bar is independent."""
        closes = [100.0, 101.0, 101.0, 102.0]
        vols = [1000.0, 500.0, 750.0, 250.0]
        out = net_volume(closes, vols)
        # i=1: close went up → +500 (not +1500)
        assert out[1] == pytest.approx(500.0)
        # i=2: unchanged → 0
        assert out[2] == pytest.approx(0.0)
        # i=3: close went up → +250 (not cumulative)
        assert out[3] == pytest.approx(250.0)


# ── volume_osc ────────────────────────────────────────────────────────────────

class TestVolumeOsc:
    def test_length_aligned(self):
        vols = _const(40, 1000.0)
        out = volume_osc(vols, short=5, long=10)
        assert len(out) == 40

    def test_approx_zero_on_constant_volume(self):
        """Both EMAs converge to the same constant → oscillator → 0."""
        vols = _const(60, 1000.0)
        out = volume_osc(vols, short=5, long=10)
        defined = _tail(out)
        assert defined
        assert all(abs(v) < 1e-9 for v in defined), f"Expected ~0 for constant vol, got {defined}"

    def test_positive_when_short_ema_above_long_ema(self):
        """Volume is flat then spikes — short EMA reacts faster → positive osc."""
        n = 60
        # flat base then a spike at the end — short EMA picks it up faster
        vols = [1000.0] * 50 + [2000.0] * 10
        out = volume_osc(vols, short=5, long=20)
        defined = _tail(out, 5)
        assert defined
        assert all(v > 0 for v in defined)

    def test_warmup_none(self):
        vols = _const(30, 1000.0)
        out = volume_osc(vols, short=5, long=10)
        # First values must be None during warm-up
        assert out[0] is None

    def test_finite_tail(self):
        h, l, closes, vols = _synth_ohlcv(80)
        out = volume_osc(vols, short=5, long=10)
        tail = _tail(out)
        assert tail
        assert all(_finite(v) for v in tail)


# ── kvo (Klinger Volume Oscillator) ──────────────────────────────────────────

class TestKvo:
    def test_returns_two_aligned_lines(self):
        h, l, c, v = _synth_ohlcv(120)
        kvo_line, signal_line = kvo(h, l, c, v)
        assert len(kvo_line) == 120
        assert len(signal_line) == 120

    def test_tail_finite(self):
        h, l, c, v = _synth_ohlcv(120)
        kvo_line, signal_line = kvo(h, l, c, v)
        kvo_tail = _tail(kvo_line)
        sig_tail = _tail(signal_line)
        assert kvo_tail and all(_finite(x) for x in kvo_tail)
        assert sig_tail and all(_finite(x) for x in sig_tail)

    def test_warmup_none(self):
        h, l, c, v = _synth_ohlcv(120)
        kvo_line, signal_line = kvo(h, l, c, v)
        assert kvo_line[0] is None
        assert signal_line[0] is None

    def test_output_count_two(self):
        """Decorator must declare exactly 2 outputs: kvo and signal."""
        from vike_trader_app.core.indicators import base
        spec = base.get("kvo")
        assert len(spec.outputs) == 2

    def test_signal_lags_kvo(self):
        """Signal is EMA of kvo so it starts at a later index."""
        h, l, c, v = _synth_ohlcv(120)
        kvo_line, signal_line = kvo(h, l, c, v)
        # Find first defined index
        kvo_first = next((i for i, x in enumerate(kvo_line) if x is not None), None)
        sig_first = next((i for i, x in enumerate(signal_line) if x is not None), None)
        assert kvo_first is not None
        assert sig_first is not None
        assert sig_first >= kvo_first


# ── relative_volatility ───────────────────────────────────────────────────────

class TestRelativeVolatility:
    def test_length_aligned(self):
        closes = _ramp(80, step=0.5)
        out = relative_volatility(closes, period=14)
        assert len(out) == 80

    def test_in_range_0_100(self):
        """RSI-like indicator must always be in [0, 100]."""
        h, l, closes, v = _synth_ohlcv(120)
        out = relative_volatility(closes, period=14)
        defined = [x for x in out if x is not None]
        assert defined
        assert all(0.0 <= x <= 100.0 for x in defined), (
            f"relative_volatility outside [0,100]: min={min(defined):.3f} max={max(defined):.3f}"
        )

    def test_warmup_none(self):
        closes = _ramp(40)
        out = relative_volatility(closes, period=14)
        assert out[0] is None

    def test_finite_tail(self):
        h, l, closes, v = _synth_ohlcv(100)
        out = relative_volatility(closes, period=14)
        tail = _tail(out)
        assert tail and all(_finite(x) for x in tail)

    def test_output_name_rvi(self):
        """Registered output name must be 'rvi'."""
        from vike_trader_app.core.indicators import base
        spec = base.get("relative_volatility")
        assert spec.outputs == ["rvi"]

    def test_mostly_rising_gives_high_rvi(self):
        """Consistently rising prices → upward stddev dominates → rvi > 50."""
        closes = _ramp(80, start=100.0, step=1.0)
        out = relative_volatility(closes, period=14)
        tail = _tail(out, 20)
        assert tail and all(x > 50.0 for x in tail)


# ── high_low_52w ──────────────────────────────────────────────────────────────

class TestHighLow52w:
    def test_length_aligned(self):
        h, l, c, v = _synth_ohlcv(300)
        high_n, low_n = high_low_52w(h, l, period=252)
        assert len(high_n) == 300
        assert len(low_n) == 300

    def test_high_n_ge_low_n(self):
        """Rolling max of highs must always be >= rolling min of lows."""
        h, l, c, v = _synth_ohlcv(300)
        high_n, low_n = high_low_52w(h, l, period=252)
        defined = [(hi, lo) for hi, lo in zip(high_n, low_n) if hi is not None and lo is not None]
        assert defined
        assert all(hi >= lo for hi, lo in defined), "high_n < low_n somewhere!"

    def test_warmup_none(self):
        n = 50
        highs = _ramp(n, 101.0)
        lows = _ramp(n, 99.0)
        out_h, out_l = high_low_52w(highs, lows, period=30)
        assert out_h[0] is None
        assert out_l[0] is None

    def test_output_names(self):
        from vike_trader_app.core.indicators import base
        spec = base.get("high_low_52w")
        assert spec.outputs == ["high_n", "low_n"]

    def test_high_n_is_running_max(self):
        """high_n must equal max(highs[i-p+1..i]) exactly."""
        highs = [float(i) for i in range(1, 21)]  # [1,2,...,20]
        lows = [0.5] * 20
        period = 5
        out_h, out_l = high_low_52w(highs, lows, period=period)
        # at i=4 (first window): max(1..5) = 5
        assert out_h[4] == pytest.approx(5.0)
        # at i=9: max(6..10) = 10
        assert out_h[9] == pytest.approx(10.0)
        # at i=19: max(16..20) = 20
        assert out_h[19] == pytest.approx(20.0)

    def test_low_n_is_running_min(self):
        """low_n must equal min(lows[i-p+1..i]) exactly."""
        highs = [200.0] * 20
        lows = [float(20 - i) for i in range(20)]  # [20,19,...,1]
        period = 5
        out_h, out_l = high_low_52w(highs, lows, period=period)
        # at i=4: min(20,19,18,17,16)=16
        assert out_l[4] == pytest.approx(16.0)
        # at i=19: min(5,4,3,2,1)=1
        assert out_l[19] == pytest.approx(1.0)


# ── registration ─────────────────────────────────────────────────────────────

class TestRegistration:
    def test_all_registered(self):
        from vike_trader_app.core.indicators import base
        names = {s.name for s in base.list_indicators()}
        expected = {"kvo", "net_volume", "volume_osc", "relative_volatility", "high_low_52w"}
        missing = expected - names
        assert not missing, f"Not registered: {missing}"

    def test_kvo_category_volume(self):
        from vike_trader_app.core.indicators import base
        assert base.get("kvo").category == "volume"

    def test_relative_volatility_category_volatility(self):
        from vike_trader_app.core.indicators import base
        assert base.get("relative_volatility").category == "volatility"
