"""Chart-data helper tests (Qt-free prep for plotting)."""

import pytest

from vike_trader_app.core.model import Bar, Trade
from vike_trader_app.ui.chartdata import (
    Marker,
    axis_time_label,
    bar_spacing,
    equity_points,
    ohlc_legend_text,
    oscillator_reveal,
    series_slice,
    trade_markers,
    ts_to_x,
    x_to_ts,
)


class _Ind:
    def __init__(self, uid, series, shown=True, bands=()):
        self.uid, self.series, self.shown, self.bands = uid, series, shown, bands


def test_series_slice_drops_none_and_caps_at_index():
    assert series_slice([1.0, None, 3.0, 4.0], 2) == ([0, 2], [1.0, 3.0])
    assert series_slice([1.0, 2.0], 10) == ([0, 1], [1.0, 2.0])   # index beyond len is capped
    assert series_slice([], 5) == ([], [])


def test_oscillator_reveal_plots_last_and_window_yrange():
    ind = _Ind("u1", {"RSI": [10.0, 20.0, 80.0, 90.0]}, shown=True, bands=[("ob", 70.0)])
    plots, lasts, y_range = oscillator_reveal(
        [ind], {"u1": ["RSI"]}, index=3, win_lo=2, win_hi=3)
    assert plots["u1"]["RSI"] == ([0, 1, 2, 3], [10.0, 20.0, 80.0, 90.0])
    assert lasts["u1"] == 90.0                       # last revealed base value
    assert y_range == (70.0, 90.0)                   # window [2,3] = {80,90} + band 70 -> (70, 90)


def test_oscillator_reveal_ma_label_not_used_for_last():
    ind = _Ind("u", {"MACD": [1.0, 2.0], "MA": [9.0, 9.0]})
    _plots, lasts, _yr = oscillator_reveal([ind], {"u": ["MACD", "MA"]}, index=1, win_lo=0, win_hi=1)
    assert lasts["u"] == 2.0                          # legend value stays on the base output, not MA


def test_oscillator_reveal_hidden_indicator_excluded_from_yrange():
    ind = _Ind("u", {"X": [5.0, 500.0]}, shown=False)
    _plots, _lasts, y_range = oscillator_reveal([ind], {"u": ["X"]}, index=1, win_lo=0, win_hi=1)
    assert y_range is None                            # hidden -> contributes nothing to the range


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
    assert axis_time_label(_obars(t0=0), 0) == "Jan 01"             # midnight -> date label
    assert axis_time_label(_obars(t0=10 * 3_600_000), 0) == "10:00"  # 10:00 UTC -> time label
    assert axis_time_label([], 0) == ""


def test_ohlc_legend_text():
    # TradingView/TradeLocker look: thousands separators + magnitude-scaled decimals.
    b = Bar(ts=0, open=100, high=110, low=95, close=105)
    assert ohlc_legend_text(b).startswith("O100.00  H110.00  L95.00  C105.00")
    assert "+5.00" in ohlc_legend_text(b, prev_close=100)
    assert ohlc_legend_text(None) == ""
    # large prices get grouped with commas (and stay at 2 dp)
    btc = Bar(ts=0, open=73182.49, high=73252.30, low=73160.20, close=73217.77)
    assert ohlc_legend_text(btc).startswith("O73,182.49  H73,252.30  L73,160.20  C73,217.77")
