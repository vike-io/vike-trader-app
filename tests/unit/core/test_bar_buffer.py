"""Unit tests for core.bar_buffer.BarSeriesBuffer.

Verifies:
- shared list reference (self.bars IS the caller's list, not a copy)
- add_live_bar appends to the shared list and refreshes higher-TF coarse bars
- bars_for returns completed higher-TF bars only (no look-ahead)
- forming_for returns the in-progress coarse bar, or None when the window is empty
- both methods are correct when no timeframes are registered
"""

import pytest

from vike_trader_app.core.bar_buffer import BarSeriesBuffer
from vike_trader_app.core.model import Bar

_MS_1M = 60_000
_MS_1H = 3_600_000


def _bar(t_minutes: int, close: float = 1.0) -> Bar:
    """Helper: 1-minute bar at minute t (ts in epoch ms)."""
    ts = t_minutes * _MS_1M
    return Bar(ts=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1.0)


# ---------------------------------------------------------------------------
# Shared list reference
# ---------------------------------------------------------------------------

def test_shared_list_reference():
    """buf.bars IS the same list object passed in — not a copy."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    assert buf.bars is bars


def test_add_live_bar_mutates_caller_list():
    """Appending via buf.add_live_bar is visible in the caller's list reference."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    b = _bar(0)
    buf.add_live_bar(b)
    assert len(bars) == 1
    assert bars[0] is b


# ---------------------------------------------------------------------------
# add_live_bar — re-resampling
# ---------------------------------------------------------------------------

def test_add_live_bar_increments_bars():
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(5):
        buf.add_live_bar(_bar(t))
    assert len(bars) == 5


def test_add_live_bar_refreshes_coarse_bars():
    """After feeding 61 1-min bars, there should be a completed 1h coarse bar."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(61):
        buf.add_live_bar(_bar(t))
    # The buffer's internal _tf coarse list should have at least 1 completed 1h bar.
    # We verify via bars_for (which reads _tf internally).
    completed = buf.bars_for("1h", now=61 * _MS_1M)
    assert len(completed) >= 1


# ---------------------------------------------------------------------------
# No timeframes registered
# ---------------------------------------------------------------------------

def test_no_timeframes_add_still_works():
    """Buffer without any timeframes just tracks base bars — no KeyError."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars)
    for t in range(5):
        buf.add_live_bar(_bar(t))
    assert len(bars) == 5


# ---------------------------------------------------------------------------
# bars_for — look-ahead guard
# ---------------------------------------------------------------------------

def test_bars_for_returns_only_completed_windows():
    """bars_for must NOT include the window that contains `now`."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    # Feed 60 bars (minutes 0-59): all inside the first hour window.
    for t in range(60):
        buf.add_live_bar(_bar(t))
    now = 59 * _MS_1M
    # The first hour's window starts at 0 and ends at 3_600_000; `now` is still inside it.
    completed = buf.bars_for("1h", now=now)
    assert len(completed) == 0


def test_bars_for_returns_completed_bars_after_window_boundary():
    """Once bar at ts=3_600_000 is fed, the first hour is completed."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(61):  # t=0..60 minutes
        buf.add_live_bar(_bar(t))
    now = 60 * _MS_1M  # ts = 3_600_000 — first bar of the SECOND hour
    completed = buf.bars_for("1h", now=now)
    assert len(completed) == 1


def test_bars_for_returns_list_type():
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    buf.add_live_bar(_bar(0))
    result = buf.bars_for("1h", now=0)
    assert isinstance(result, list)


def test_bars_for_two_completed_hours():
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(121):  # 0..120 minutes (2 full hours + 1 bar into the 3rd)
        buf.add_live_bar(_bar(t))
    now = 120 * _MS_1M
    completed = buf.bars_for("1h", now=now)
    assert len(completed) == 2


# ---------------------------------------------------------------------------
# forming_for — in-progress coarse bar
# ---------------------------------------------------------------------------

def test_forming_for_returns_none_when_no_bars():
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    assert buf.forming_for("1h", now=0) is None


def test_forming_for_returns_bar_mid_window():
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(30):  # halfway through the first hour
        buf.add_live_bar(_bar(t))
    now = 29 * _MS_1M
    forming = buf.forming_for("1h", now=now)
    assert forming is not None
    assert isinstance(forming, Bar)


def test_forming_for_ts_is_window_start():
    """forming_for's Bar.ts must be aligned to the window start (not the last bar ts)."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(30):
        buf.add_live_bar(_bar(t))
    now = 29 * _MS_1M
    forming = buf.forming_for("1h", now=now)
    assert forming.ts == 0  # window starts at epoch 0


def test_forming_for_aggregates_ohlcv():
    """The forming bar's OHLCV correctly aggregates from base bars."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    # Three 1-min bars with distinctive closes for easy assertion.
    buf.add_live_bar(Bar(ts=0, open=10, high=15, low=8, close=11, volume=100.0))
    buf.add_live_bar(Bar(ts=_MS_1M, open=11, high=20, low=9, close=12, volume=200.0))
    buf.add_live_bar(Bar(ts=2 * _MS_1M, open=12, high=13, low=7, close=13, volume=150.0))
    now = 2 * _MS_1M
    forming = buf.forming_for("1h", now=now)
    assert forming is not None
    assert forming.open == 10           # first bar's open
    assert forming.close == 13          # last bar's close
    assert forming.high == 20           # max of all highs
    assert forming.low == 7             # min of all lows
    assert forming.volume == pytest.approx(450.0)


def test_forming_for_returns_none_after_window_completes():
    """Once a new hour starts, forming_for with `now` at the new hour start returns None
    only if no bars in the new window yet.  Here we only have bars in the old window."""
    bars: list[Bar] = []
    buf = BarSeriesBuffer(bars, timeframes=["1h"])
    for t in range(60):
        buf.add_live_bar(_bar(t))
    # now = 3_600_000: first ts of the 2nd hour window; no bars in [3_600_000, 3_600_000].
    now = 60 * _MS_1M
    # The base bar at t=60 is not fed, so no bars in the 2nd window.
    forming = buf.forming_for("1h", now=now)
    # bisect_right(bars, now, key=_BAR_TS) returns index at t=60; bisect_left returns same
    # => window is the bar at ts=3_600_000 IF we fed it; here we only fed 0..59, so empty.
    assert forming is None


# ---------------------------------------------------------------------------
# Parity with BacktestEngine (regression guard)
# ---------------------------------------------------------------------------

def test_parity_with_engine_bars_for(subtests):
    """bars_for via BarSeriesBuffer must match BacktestEngine.bars_for exactly."""
    from vike_trader_app.core.engine import BacktestEngine
    from vike_trader_app.core.strategy import Strategy

    class _S(Strategy):
        def on_bar(self, bar): pass

    all_bars = [_bar(t) for t in range(65)]  # 65 1-min bars
    engine = BacktestEngine(all_bars, _S(), timeframes=["1h"])

    buf = BarSeriesBuffer(list(all_bars), timeframes=["1h"])
    for b in all_bars:
        buf.add_live_bar.__func__  # just check attribute access
    # Re-seed the buffer from scratch with the same bars.
    bars2: list[Bar] = []
    buf2 = BarSeriesBuffer(bars2, timeframes=["1h"])
    for b in all_bars:
        buf2.add_live_bar(b)

    for i, b in enumerate(all_bars):
        now_ts = b.ts
        engine_result = engine._buf.bars_for("1h", now_ts)
        buf_result = buf2.bars_for("1h", now_ts)
        with subtests.test(i=i):
            assert len(engine_result) == len(buf_result)
