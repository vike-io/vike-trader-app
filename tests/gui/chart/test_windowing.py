"""Chart windowing helpers (Qt-free): default view + replay-follow + visible y-bounds."""

from vike_trader_app.core.model import Bar
from vike_trader_app.ui.chartdata import follow_window, y_bounds


def _bars(n):
    return [
        Bar(ts=i, open=10 + i, high=12 + i, low=8 + i, close=11 + i, volume=1.0) for i in range(n)
    ]


def test_follow_window_keeps_cursor_visible():
    lo, hi = follow_window(500, 1000, 300)
    assert lo <= 500 <= hi
    assert hi - lo == 300  # full width when there's room on both sides


def test_follow_window_clamps_start():
    lo, hi = follow_window(10, 1000, 300)
    assert lo == 0


def test_follow_window_clamps_end():
    lo, hi = follow_window(995, 1000, 300)
    assert hi == 1000


def test_y_bounds_over_visible_slice():
    bars = _bars(100)
    # bars[10:20]: lows 18..27, highs 22..31
    assert y_bounds(bars, 10, 20) == (18, 31)


def test_y_bounds_empty_slice_is_none():
    assert y_bounds(_bars(5), 10, 20) is None
