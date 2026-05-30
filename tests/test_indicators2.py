"""Expanded native indicators (TA-Lib-rivalling set) + indicator factory."""

import pytest

from vike_trader_app.core.indicators import (
    adx,
    cci,
    expand,
    keltner,
    obv,
    roc,
    true_range,
    williams_r,
    wma,
)


def test_wma_weights_recent_more():
    out = wma([1, 2, 3], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx((1 * 1 + 2 * 2 + 3 * 3) / 6)  # 14/6


def test_roc_percent_change():
    out = roc([100, 110, 99], 1)
    assert out[0] is None
    assert out[1] == pytest.approx(10.0)
    assert out[2] == pytest.approx(-10.0)


def test_true_range_first_is_range_then_gap_aware():
    highs = [10, 12, 11]
    lows = [8, 9, 7]
    closes = [9, 11, 8]
    tr = true_range(highs, lows, closes)
    assert tr[0] == pytest.approx(2.0)                  # 10-8
    assert tr[1] == pytest.approx(max(12 - 9, abs(12 - 9), abs(9 - 9)))  # vs prev close 9


def test_williams_r_bounds():
    highs = [10] * 5
    lows = [5] * 5  # flat band -> window HH=10, LL=5 unambiguously
    out = williams_r(highs, lows, [10] * 5, period=3)   # close at high
    assert out[-1] == pytest.approx(0.0)
    out2 = williams_r(highs, lows, [5] * 5, period=3)   # close at low
    assert out2[-1] == pytest.approx(-100.0)


def test_obv_accumulates_signed_volume():
    out = obv([10, 11, 10, 12], [100, 50, 30, 40])
    assert out[-1] == pytest.approx(50 - 30 + 40)  # up,down,up = 60


def test_keltner_orders_bands():
    n = 30
    highs = [100 + i for i in range(n)]
    lows = [98 + i for i in range(n)]
    closes = [99 + i for i in range(n)]
    upper, mid, lower = keltner(highs, lows, closes, period=20, mult=2.0)
    i = n - 1
    assert upper[i] > mid[i] > lower[i]


def test_adx_returns_three_aligned_bounded_lines():
    n = 40
    highs = [100 + (i % 5) for i in range(n)]
    lows = [98 + (i % 5) for i in range(n)]
    closes = [99 + (i % 5) for i in range(n)]
    adx_line, plus_di, minus_di = adx(highs, lows, closes, period=14)
    assert len(adx_line) == len(plus_di) == len(minus_di) == n
    defined = [v for v in plus_di if v is not None]
    assert defined and all(0.0 <= v <= 100.0 for v in defined)


def test_cci_aligned():
    n = 30
    highs = [100 + i for i in range(n)]
    lows = [98 + i for i in range(n)]
    closes = [99 + i for i in range(n)]
    out = cci(highs, lows, closes, period=20)
    assert len(out) == n
    assert out[0] is None and out[-1] is not None


def test_expand_factory_runs_indicator_over_param_grid():
    from vike_trader_app.core.indicators import sma

    grid = expand(sma, [1, 2, 3, 4, 5], [2, 3])
    assert set(grid) == {2, 3}
    assert grid[2][-1] == pytest.approx(4.5)  # sma period 2 of last two (4,5)
    assert grid[3][-1] == pytest.approx(4.0)  # sma period 3 of (3,4,5)
