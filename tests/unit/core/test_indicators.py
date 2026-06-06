"""Indicator-library tests (Qt-free pure functions)."""

import pytest

from vike_trader_app.core.indicators import (
    atr,
    bollinger,
    donchian,
    ema,
    macd,
    rsi,
    sma,
    stochastic,
    vwap,
)


def test_sma_warmup_is_none_then_average():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out[0] is None
    assert out[1] is None
    assert out[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert out[3] == pytest.approx(3.0)  # (2+3+4)/3
    assert out[4] == pytest.approx(4.0)  # (3+4+5)/3


def test_sma_period_one_is_identity():
    assert sma([5, 6, 7], 1) == [5, 6, 7]


def test_sma_length_matches_input():
    assert len(sma([1, 2, 3, 4], 2)) == 4


def test_ema_seeds_with_sma_then_recurses():
    # period 2: seed at index1 = (1+2)/2 = 1.5; then EMA mult = 2/3
    out = ema([1, 2, 3], 2)
    assert out[0] is None
    assert out[1] == pytest.approx(1.5)
    assert out[2] == pytest.approx(3 * (2 / 3) + 1.5 * (1 / 3))


def test_rsi_all_gains_is_100():
    out = rsi([1, 2, 3, 4, 5, 6], 3)
    assert out[-1] == pytest.approx(100.0)


def test_rsi_warmup_is_none():
    out = rsi([1, 2, 3], 5)
    assert out[0] is None
    assert all(v is None for v in out)


def test_macd_constant_series_is_zero_where_defined():
    line, signal, hist = macd([5.0] * 40, fast=12, slow=26, signal=9)
    assert len(line) == len(signal) == len(hist) == 40
    # constant series -> both EMAs equal -> macd line 0 where both are warmed up
    defined = [v for v in line if v is not None]
    assert defined and all(v == pytest.approx(0.0) for v in defined)
    h = [v for v in hist if v is not None]
    assert h and all(v == pytest.approx(0.0) for v in h)


def test_bollinger_constant_series_collapses_to_mid():
    upper, mid, lower = bollinger([10.0] * 25, period=20, k=2.0)
    assert mid[19] == pytest.approx(10.0)  # SMA of constants
    assert upper[19] == pytest.approx(10.0) and lower[19] == pytest.approx(10.0)  # std 0
    assert mid[0] is None and upper[0] is None


def test_atr_constant_range_equals_range():
    n = 20
    highs = [10.0] * n
    lows = [8.0] * n
    closes = [9.0] * n  # within [low, high], constant -> TR == high-low == 2
    out = atr(highs, lows, closes, period=14)
    assert out[-1] == pytest.approx(2.0)
    assert out[0] is None


def test_stochastic_close_at_high_is_100():
    n = 20
    highs = [100 + i for i in range(n)]
    lows = [90 + i for i in range(n)]
    closes = highs[:]  # close == high -> %K == 100
    k, d = stochastic(highs, lows, closes, k_period=14, d_period=3)
    assert k[-1] == pytest.approx(100.0)
    assert d[-1] == pytest.approx(100.0)


def test_vwap_constant_typical_price():
    n = 5
    highs = [11.0] * n
    lows = [7.0] * n
    closes = [9.0] * n  # typical = (11+7+9)/3 = 9
    vols = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = vwap(highs, lows, closes, vols)
    assert out[-1] == pytest.approx(9.0)
    assert out[0] == pytest.approx(9.0)


def test_donchian_tracks_window_extremes():
    highs = [1, 3, 2, 5, 4]
    lows = [0, 1, 1, 2, 3]
    upper, mid, lower = donchian(highs, lows, period=3)
    # at index 3, window highs [3,2,5]->5, lows [1,1,2]->1, mid 3
    assert upper[3] == pytest.approx(5.0)
    assert lower[3] == pytest.approx(1.0)
    assert mid[3] == pytest.approx(3.0)
    assert upper[1] is None  # warmup
