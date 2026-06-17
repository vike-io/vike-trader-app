"""Chart-data helper tests (Qt-free prep for plotting)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.ui.chartdata import (
    axis_time_label,
    bar_spacing,
    oscillator_reveal,
    series_slice,
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
