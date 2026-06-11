"""Unit tests for the Qt-free dashboard-tile data helpers."""

from dataclasses import dataclass

from vike_trader_app.ui.dashtiles_data import (
    age_label,
    day_bounds_utc,
    latest_headlines,
    pnl_summary,
    today_events,
    top_movers,
)


def test_top_movers_ranks_by_abs_change():
    prices = {
        "BTCUSDT": (62_000.0, 0.01),
        "ETHUSDT": (2_400.0, -0.05),
        "SOLUSDT": (150.0, 0.03),
        "ADAUSDT": (0.45, None) and None,   # missing quote -> skipped
    }
    rows = top_movers(prices, n=2)
    assert [r[0] for r in rows] == ["ETHUSDT", "SOLUSDT"]   # |−5%| > |3%| > |1%|
    assert rows[0] == ("ETHUSDT", 2_400.0, -0.05)


def test_top_movers_empty():
    assert top_movers({}) == []


def test_pnl_summary_math_and_final_override():
    s = pnl_summary([10_000.0, 10_500.0, 10_250.0])
    assert s["initial"] == 10_000.0 and s["equity"] == 10_250.0
    assert s["pnl"] == 250.0 and abs(s["ret_pct"] - 2.5) < 1e-9
    s2 = pnl_summary([10_000.0, 10_500.0], final_equity=11_000.0)
    assert s2["equity"] == 11_000.0 and s2["pnl"] == 1_000.0
    assert pnl_summary([]) is None


def test_day_bounds_utc_covers_exactly_one_day():
    # 2026-06-11 13:30:00 UTC
    now_ms = 1_781_098_200_000
    lo, hi = day_bounds_utc(now_ms)
    assert hi - lo == 86_400_000
    assert lo <= now_ms < hi
    assert lo % 1000 == 0


@dataclass
class _Ev:
    ts_utc: int
    importance: int
    title: str = ""


def test_today_events_filters_sorts_and_caps():
    now = 1_781_098_200_000
    lo, hi = day_bounds_utc(now)
    events = [
        _Ev(lo - 1, 2),                  # yesterday -> excluded
        _Ev(hi, 2),                      # tomorrow -> excluded
        _Ev(lo + 3_600_000, 0, "low"),
        _Ev(lo + 3_600_000, 2, "high"),  # same minute: high importance first
        _Ev(lo + 60_000, 1, "first"),
    ]
    got = today_events(events, now)
    assert [e.title for e in got] == ["first", "high", "low"]
    assert len(today_events(events, now, n=1)) == 1


@dataclass
class _Item:
    published_ms: int
    title: str = ""


def test_latest_headlines_newest_first():
    items = [_Item(100, "old"), _Item(300, "new"), _Item(200, "mid")]
    got = latest_headlines(items, n=2)
    assert [i.title for i in got] == ["new", "mid"]


def test_age_label_buckets():
    now = 10 * 86_400_000
    assert age_label(now - 5 * 60_000, now) == "5m"
    assert age_label(now - 3 * 3_600_000, now) == "3h"
    assert age_label(now - 2 * 86_400_000, now) == "2d"
    assert age_label(now + 60_000, now) == "0m"   # future-dated clamps to 0
