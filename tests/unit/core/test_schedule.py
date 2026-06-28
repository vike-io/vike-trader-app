from vike_trader_app.core.schedule import (
    EveryNBars, MonthStart, PeriodStart, Schedule, WeekStart,
)

import pytest


def test_period_start_fires_once_per_month():
    r = PeriodStart("monthly")
    # 2024-01-15, 2024-01-20 (same month), 2024-02-01, 2024-03-05
    tss = [1705276800000, 1705708800000, 1706745600000, 1709596800000]
    due = [r.is_due(ts, i) for i, ts in enumerate(tss)]
    assert due == [True, False, True, True]   # first-seen month, same month, new, new

def test_month_start_is_period_start_monthly():
    a, b = MonthStart(), PeriodStart("monthly")
    tss = [1705276800000, 1706745600000]
    assert [a.is_due(t, i) for i, t in enumerate(tss)] == [b.is_due(t, i) for i, t in enumerate(tss)]

def test_every_n_bars():
    r = EveryNBars(3)
    assert [r.is_due(0, i) for i in range(7)] == [True, False, False, True, False, False, True]

def test_schedule_fires_due_callbacks_once_per_bar():
    fired = []
    s = Schedule()
    s.on(EveryNBars(2), lambda: fired.append("a"))
    s.on(MonthStart(), lambda: fired.append("m"))
    # bar 0 (Jan): EveryNBars(2) due (0%2==0) + MonthStart due (first month)
    cbs = s.check_due(1705276800000, 0)
    for cb in cbs: cb()
    assert fired == ["a", "m"]
    # same bar again -> nothing new (per-bar de-dup)
    assert s.check_due(1705276800000, 0) == []
    # bar 1 (same month) -> neither due
    fired.clear()
    for cb in s.check_due(1705708800000, 1): cb()
    assert fired == []


def test_every_n_bars_zero_raises():
    with pytest.raises(ValueError):
        EveryNBars(0)
