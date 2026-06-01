"""Chart-data helper tests (Qt-free prep for plotting)."""

import pytest

from vike_trader_app.core.model import Bar, Trade
from vike_trader_app.ui.chartdata import (
    Marker,
    axis_time_label,
    bar_spacing,
    equity_points,
    ohlc_legend_text,
    trade_markers,
    ts_to_x,
    x_to_ts,
)


def test_trade_markers_emit_entry_then_exit():
    t = Trade(entry_price=100, exit_price=110, size=1, pnl=10, entry_ts=1, exit_ts=2)
    assert trade_markers([t]) == [
        Marker(ts=1, price=100, kind="entry"),
        Marker(ts=2, price=110, kind="exit"),
    ]


def test_trade_markers_empty():
    assert trade_markers([]) == []


def test_equity_points_zips_ts_and_equity():
    xs, ys = equity_points([10, 20, 30], [100.0, 101.0, 102.0])
    assert xs == [10, 20, 30]
    assert ys == [100.0, 101.0, 102.0]


def test_equity_points_length_mismatch_raises():
    with pytest.raises(ValueError):
        equity_points([1, 2], [100.0])


def _obars(n=5, step=60_000, t0=1_000_000):
    return [Bar(ts=t0 + i * step, open=10 + i, high=11 + i, low=9 + i, close=10.5 + i)
            for i in range(n)]


def test_bar_spacing():
    assert bar_spacing(_obars()) == 60_000
    assert bar_spacing(_obars(1)) == 0
    assert bar_spacing([]) == 0


def test_ts_x_roundtrip():
    bars = _obars()
    assert ts_to_x(bars, bars[0].ts) == 0.0
    assert ts_to_x(bars, bars[3].ts) == 3.0
    assert x_to_ts(bars, 3.0) == bars[3].ts
    assert x_to_ts(bars, 4.5) == bars[0].ts + int(4.5 * 60_000)


def test_ts_x_empty_safe():
    assert ts_to_x([], 5) == 0.0
    assert x_to_ts([], 7) == 7


def test_axis_time_label_utc():
    bars = _obars(t0=0)  # epoch 0 = 1970-01-01 00:00 UTC
    assert axis_time_label(bars, 0) == "01-01 00:00"
    assert axis_time_label([], 0) == ""


def test_ohlc_legend_text():
    b = Bar(ts=0, open=100, high=110, low=95, close=105)
    assert ohlc_legend_text(b).startswith("O100  H110  L95  C105")
    assert "+5" in ohlc_legend_text(b, prev_close=100)
    assert ohlc_legend_text(None) == ""
