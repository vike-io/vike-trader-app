"""Tests for analysis/periods.py — periodic returns and drawdown table."""

from datetime import datetime, timezone

import pytest

from vike_trader_app.analysis.periods import (
    drawdown_table,
    monthly_return_matrix,
    periodic_returns,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(year: int, month: int, day: int) -> int:
    """Epoch-ms for midnight UTC on the given date."""
    dt = datetime(year, month, day, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# A simple 3-month equity curve: Jan 2024, Feb 2024, Mar 2024
# Two bars per month is sufficient to exercise the grouping logic.
_JAN_EQ = [10_000.0, 10_200.0]
_FEB_EQ = [10_100.0, 10_500.0]
_MAR_EQ = [10_300.0, 10_800.0]

_3MO_EQUITY = _JAN_EQ + _FEB_EQ + _MAR_EQ
_3MO_TS = [
    _ts(2024, 1, 15), _ts(2024, 1, 25),
    _ts(2024, 2, 10), _ts(2024, 2, 25),
    _ts(2024, 3, 10), _ts(2024, 3, 25),
]


# ---------------------------------------------------------------------------
# periodic_returns()
# ---------------------------------------------------------------------------

def test_monthly_three_entries():
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="monthly")
    assert len(result) == 3


def test_monthly_labels():
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="monthly")
    labels = [r[0] for r in result]
    assert labels == ["2024-01", "2024-02", "2024-03"]


def test_monthly_jan_return_correct():
    """Jan return: last_jan / first_jan_entry - 1 = 10200/10000 - 1 = 2%."""
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="monthly")
    _, jan_ret = result[0]
    assert jan_ret == pytest.approx(0.02)


def test_monthly_feb_return_correct():
    """Feb entry equity is the last Jan equity (10200).  Feb return = 10500/10200 - 1."""
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="monthly")
    _, feb_ret = result[1]
    assert feb_ret == pytest.approx(10_500.0 / 10_200.0 - 1.0)


def test_monthly_mar_return_correct():
    """Mar entry equity is the last Feb equity (10500).  Mar return = 10800/10500 - 1."""
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="monthly")
    _, mar_ret = result[2]
    assert mar_ret == pytest.approx(10_800.0 / 10_500.0 - 1.0)


def test_monthly_returns_signs_positive_for_rising_curve():
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="monthly")
    assert all(r > 0 for _, r in result)


def test_yearly_single_year():
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="yearly")
    assert len(result) == 1
    assert result[0][0] == "2024"


def test_daily_returns_count():
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="daily")
    # Each timestamp is a distinct day → 6 labels
    assert len(result) == 6


def test_periodic_returns_empty():
    assert periodic_returns([], [], period="monthly") == []


def test_periodic_returns_length_mismatch_raises():
    with pytest.raises(ValueError):
        periodic_returns([100.0, 110.0], [_ts(2024, 1, 1)], period="monthly")


def test_periodic_returns_invalid_period_raises():
    with pytest.raises(ValueError):
        periodic_returns([100.0], [_ts(2024, 1, 1)], period="quarterly")


def test_weekly_period_labels_start_with_year():
    result = periodic_returns(_3MO_EQUITY, _3MO_TS, period="weekly")
    for label, _ in result:
        assert "-W" in label


# ---------------------------------------------------------------------------
# monthly_return_matrix()
# ---------------------------------------------------------------------------

def test_matrix_has_correct_keys():
    m = monthly_return_matrix(_3MO_EQUITY, _3MO_TS)
    assert set(m.keys()) == {"years", "matrix", "annual"}


def test_matrix_years():
    m = monthly_return_matrix(_3MO_EQUITY, _3MO_TS)
    assert m["years"] == [2024]


def test_matrix_months_present():
    m = monthly_return_matrix(_3MO_EQUITY, _3MO_TS)
    months_2024 = m["matrix"][2024]
    assert 1 in months_2024
    assert 2 in months_2024
    assert 3 in months_2024


def test_matrix_annual_2024_positive():
    m = monthly_return_matrix(_3MO_EQUITY, _3MO_TS)
    assert m["annual"][2024] > 0


def test_matrix_multi_year():
    eq = [10_000.0, 10_500.0, 9_800.0, 10_200.0]
    ts = [
        _ts(2023, 12, 15),
        _ts(2023, 12, 29),
        _ts(2024, 1, 10),
        _ts(2024, 1, 25),
    ]
    m = monthly_return_matrix(eq, ts)
    assert set(m["years"]) == {2023, 2024}


# ---------------------------------------------------------------------------
# drawdown_table()
# ---------------------------------------------------------------------------

def _dd_curve():
    """A curve with one clear drawdown: peak at 120, trough at 90, never recovered."""
    eq = [100.0, 110.0, 120.0, 100.0, 90.0, 95.0]
    ts = [
        _ts(2024, 1, 1),
        _ts(2024, 1, 2),
        _ts(2024, 1, 3),
        _ts(2024, 1, 4),
        _ts(2024, 1, 5),
        _ts(2024, 1, 6),
    ]
    return eq, ts


def test_dd_table_one_drawdown_found():
    eq, ts = _dd_curve()
    table = drawdown_table(eq, ts, top_n=5)
    assert len(table) == 1


def test_dd_table_depth_correct():
    eq, ts = _dd_curve()
    table = drawdown_table(eq, ts)
    # Peak 120, trough 90 → depth = (120 - 90) / 120 = 0.25
    assert table[0]["depth"] == pytest.approx(0.25)


def test_dd_table_peak_ts():
    eq, ts = _dd_curve()
    table = drawdown_table(eq, ts)
    assert table[0]["peak_ts"] == ts[2]  # index 2 → 2024-01-03


def test_dd_table_trough_ts():
    eq, ts = _dd_curve()
    table = drawdown_table(eq, ts)
    assert table[0]["trough_ts"] == ts[4]  # index 4 → 2024-01-05


def test_dd_table_no_recovery():
    """Never recovers to 120 → recovery_ts is None."""
    eq, ts = _dd_curve()
    table = drawdown_table(eq, ts)
    assert table[0]["recovery_ts"] is None
    assert table[0]["recovery"] is None


def test_dd_table_length_bars():
    eq, ts = _dd_curve()
    table = drawdown_table(eq, ts)
    # Peak idx=2, trough idx=4 → length = 4 - 2 = 2
    assert table[0]["length"] == 2


def test_dd_table_with_recovery():
    """Curve recovers → recovery_ts should be set."""
    eq = [100.0, 120.0, 90.0, 130.0]
    ts = [_ts(2024, 1, i + 1) for i in range(4)]
    table = drawdown_table(eq, ts)
    assert len(table) == 1
    assert table[0]["recovery_ts"] == ts[3]
    assert table[0]["recovery"] == 1  # 1 bar from trough (idx=2) to recovery (idx=3)


def test_dd_table_monotone_up_is_empty():
    eq = [100.0, 110.0, 120.0, 130.0]
    ts = [_ts(2024, 1, i + 1) for i in range(4)]
    assert drawdown_table(eq, ts) == []


def test_dd_table_empty_curve():
    assert drawdown_table([], []) == []


def test_dd_table_top_n_limits():
    # Build 3 small drawdowns: 10%, 20%, 30%
    eq = [
        100.0, 90.0, 100.0,   # 10% dd, recovers
        110.0, 88.0, 110.0,   # 20% dd, recovers
        120.0, 84.0, 120.0,   # 30% dd, recovers
    ]
    ts = [_ts(2024, 1, i + 1) for i in range(9)]
    table = drawdown_table(eq, ts, top_n=2)
    assert len(table) == 2
    assert table[0]["depth"] >= table[1]["depth"]


def test_dd_table_sorted_by_depth_descending():
    eq = [
        100.0, 90.0, 100.0,   # 10% dd, recovers
        110.0, 77.0, 110.0,   # 30% dd, recovers
        120.0, 102.0, 120.0,  # 15% dd, recovers
    ]
    ts = [_ts(2024, 1, i + 1) for i in range(9)]
    table = drawdown_table(eq, ts, top_n=5)
    depths = [e["depth"] for e in table]
    assert depths == sorted(depths, reverse=True)


def test_dd_table_length_mismatch_raises():
    with pytest.raises(ValueError):
        drawdown_table([100.0, 90.0], [_ts(2024, 1, 1)], top_n=5)
