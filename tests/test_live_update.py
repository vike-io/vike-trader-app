"""live_update: pure helpers behind the auto-updating chart + connection watchdog.

``merge_live_bars`` folds a small "latest bars" fetch (which ends with the still-forming
candle) into the displayed series — replacing the last bar when it ticks, appending when a
candle rolls over, de-duping/sorting otherwise. ``feed_health`` classifies the feed as
live / stale / down from the newest bar's age and the consecutive-fetch-failure streak.

Both are Qt-free and network-free, so the whole thing is deterministic and unit-testable
(matching the polling_feed / watchlist_data convention).
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.live_update import (
    closed_bars,
    feed_health,
    live_fetch_window,
    merge_live_bars,
)

_MIN = 60_000  # 1m interval in ms


def _bar(ts, c=100.0):
    return Bar(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1.0)


# --- merge_live_bars -------------------------------------------------------

def test_merge_replaces_forming_last_bar_when_it_ticks():
    # The current candle's close moved: same ts, new values -> replace in place, no append.
    existing = [_bar(0), _bar(_MIN, 101)]
    merged, appended, replaced_last = merge_live_bars(existing, [_bar(_MIN, 105)])
    assert [b.ts for b in merged] == [0, _MIN]
    assert merged[-1].close == 105
    assert appended == 0
    assert replaced_last is True


def test_merge_appends_new_bar_on_rollover():
    # Last closed bar restated unchanged + a brand-new forming candle -> one append.
    existing = [_bar(0), _bar(_MIN)]
    merged, appended, replaced_last = merge_live_bars(existing, [_bar(_MIN), _bar(2 * _MIN, 110)])
    assert [b.ts for b in merged] == [0, _MIN, 2 * _MIN]
    assert appended == 1
    assert replaced_last is False  # the restated bar was identical


def test_merge_appends_pure_new_bar_without_restating_last():
    existing = [_bar(0), _bar(_MIN)]
    merged, appended, replaced_last = merge_live_bars(existing, [_bar(2 * _MIN)])
    assert [b.ts for b in merged] == [0, _MIN, 2 * _MIN]
    assert appended == 1
    assert replaced_last is False


def test_merge_dedupes_and_sorts_out_of_order_fetch():
    existing = [_bar(0)]
    merged, appended, replaced_last = merge_live_bars(
        existing, [_bar(2 * _MIN), _bar(_MIN), _bar(0)]
    )
    assert [b.ts for b in merged] == [0, _MIN, 2 * _MIN]  # sorted ascending, deduped
    assert appended == 2  # 60k and 120k are new
    assert replaced_last is False  # the dup of ts=0 carried identical values


def test_merge_into_empty_existing():
    merged, appended, replaced_last = merge_live_bars([], [_bar(0), _bar(_MIN)])
    assert [b.ts for b in merged] == [0, _MIN]
    assert appended == 2
    assert replaced_last is False


def test_merge_empty_fetch_is_noop():
    existing = [_bar(0)]
    merged, appended, replaced_last = merge_live_bars(existing, [])
    assert merged == existing
    assert appended == 0
    assert replaced_last is False


def test_merge_identical_last_bar_is_not_flagged_as_replaced():
    # A poll that returns the same forming candle, unchanged -> caller should see "no change".
    existing = [_bar(0), _bar(_MIN, 100)]
    merged, appended, replaced_last = merge_live_bars(existing, [_bar(_MIN, 100)])
    assert appended == 0
    assert replaced_last is False


# --- feed_health -----------------------------------------------------------

def test_health_live_when_newest_within_two_intervals():
    assert feed_health(now=100_000, newest_ts=70_000, interval_ms=_MIN, fail_streak=0) == "live"


def test_health_live_at_exactly_two_intervals_then_stale_just_past():
    assert feed_health(now=2 * _MIN, newest_ts=0, interval_ms=_MIN, fail_streak=0) == "live"
    assert feed_health(now=2 * _MIN + 1, newest_ts=0, interval_ms=_MIN, fail_streak=0) == "stale"


def test_health_stale_when_data_is_old():
    assert feed_health(now=600_000, newest_ts=0, interval_ms=_MIN, fail_streak=0) == "stale"


def test_health_down_when_failure_streak_exceeds_threshold():
    # Fresh data, but repeated fetch errors -> the connection is down regardless of age.
    assert feed_health(now=100_000, newest_ts=99_000, interval_ms=_MIN, fail_streak=3) == "down"


def test_health_transient_failure_below_threshold_is_not_down():
    assert feed_health(now=100_000, newest_ts=99_000, interval_ms=_MIN, fail_streak=2) == "live"


def test_health_stale_when_no_data_yet():
    assert feed_health(now=100_000, newest_ts=None, interval_ms=_MIN, fail_streak=0) == "stale"


def test_health_down_overrides_missing_data():
    assert feed_health(now=100_000, newest_ts=None, interval_ms=_MIN, fail_streak=5) == "down"


# --- closed_bars (look-ahead safety) ---------------------------------------

def test_closed_bars_drops_still_forming_tail():
    # bar@60k closes at 120k; at now=90k it's still forming -> drop it for backtests.
    bars = [_bar(0), _bar(_MIN)]
    assert [b.ts for b in closed_bars(bars, _MIN, now=90_000)] == [0]


def test_closed_bars_keeps_all_when_tail_has_closed():
    bars = [_bar(0), _bar(_MIN)]
    assert [b.ts for b in closed_bars(bars, _MIN, now=2 * _MIN)] == [0, _MIN]
    assert [b.ts for b in closed_bars(bars, _MIN, now=2 * _MIN + 5)] == [0, _MIN]


def test_closed_bars_empty_input():
    assert closed_bars([], _MIN, now=123) == []


def test_closed_bars_single_forming_bar_becomes_empty():
    assert closed_bars([_bar(0)], _MIN, now=30_000) == []


# --- live_fetch_window (gap-aware fetch range) ------------------------------

def test_live_fetch_window_no_history_uses_lookback():
    now = 1_000_000_000
    assert live_fetch_window(None, now, _MIN, lookback=5) == (now - 5 * _MIN, now)


def test_live_fetch_window_small_gap_clamps_up_to_lookback():
    # Normal steady polling: only a bar or two elapsed -> still fetch the lookback window
    # (so the forming candle + a couple closed bars come back for tick-replace).
    now = 1_000_000_000
    assert live_fetch_window(now - 2 * _MIN, now, _MIN, lookback=5) == (now - 5 * _MIN, now)


def test_live_fetch_window_extends_to_cover_a_gap():
    # After a pause (e.g. a long Forward session) the window stretches back to bridge the gap,
    # +2 bars of margin to re-fetch the last closed bar and the forming one.
    now = 1_000_000_000
    assert live_fetch_window(now - 50 * _MIN, now, _MIN, lookback=5) == (now - 52 * _MIN, now)


def test_live_fetch_window_caps_at_max_bars():
    now = 1_000_000_000
    assert live_fetch_window(now - 5_000 * _MIN, now, _MIN, lookback=5, max_bars=1_500) == (
        now - 1_500 * _MIN, now
    )
