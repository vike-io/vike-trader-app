import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402
from vike_trader_app.data.calendar.equity import EarningsEvent, DividendEvent, IpoEvent  # noqa: E402
from vike_trader_app.ui.equity_calendar import (  # noqa: E402
    EquityCalendarTab, CalendarSpace, _earnings_cfg, _dividends_cfg, _ipo_cfg,
    _fmt_big, _fmt_cap, _fmt_pct,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _earn_tab(app, events):
    t = EquityCalendarTab(fetch=lambda f, to: list(events), **_earnings_cfg())
    t.load()
    return t


def _ev(date, sym, hour="amc", est=1.0, act=1.1, rev=1e9, cap=None):
    return EarningsEvent(date, sym, hour, est, act, rev, rev, market_cap=cap)


def test_earnings_groups_by_date_and_renders(app):
    evs = [_ev("2026-06-03", "AAPL"), _ev("2026-06-03", "MSFT"), _ev("2026-06-04", "TSLA")]
    t = _earn_tab(app, evs)
    assert t.visible_event_count() == 3
    roots = [t._tree.topLevelItem(i).text(0) for i in range(t._tree.topLevelItemCount())]
    assert any("June 3" in r for r in roots) and any("June 4" in r for r in roots)


def test_symbol_filter(app):
    t = _earn_tab(app, [_ev("2026-06-03", "AAPL"), _ev("2026-06-03", "MSFT")])
    assert t.visible_event_count() == 2
    t._on_filter("aapl")
    assert t.visible_event_count() == 1


def test_empty_shows_hint(app):
    t = _earn_tab(app, [])
    assert t.visible_event_count() == 0
    assert "no events" in t._status.text().lower()


def test_covered_only_default_hides_uncovered(app):
    evs = [_ev("2026-06-03", "AAPL", est=1.5),
           EarningsEvent("2026-06-03", "ZZZZ", "", None, None, None, None)]   # no estimate
    t = _earn_tab(app, evs)
    assert t.visible_event_count() == 1            # ZZZZ hidden by default ("Covered only")
    t._on_covered_toggled(False)
    assert t.visible_event_count() == 2


def test_earnings_sorted_big_cap_first(app):
    evs = [_ev("2026-06-03", "SMALL", cap=500), _ev("2026-06-03", "BIG", cap=200000)]
    t = _earn_tab(app, evs)
    assert t._tree.topLevelItem(0).child(0).text(1) == "BIG"   # larger market cap first


def test_surprise_property():
    assert _ev("d", "X", est=2.0, act=2.4).surprise == pytest.approx(20.0)
    assert EarningsEvent("d", "X", "", None, None, None, None).surprise is None


def test_fmt_helpers():
    assert _fmt_big(9.4e10) == "94.0B" and _fmt_big(None) == "—"
    assert _fmt_cap(24209) == "24.2B" and _fmt_cap(930) == "930M" and _fmt_cap(None) == "—"
    assert _fmt_pct(16.0) == "+16.0%" and _fmt_pct(-4.8) == "-4.8%" and _fmt_pct(None) == "—"


def test_dividends_and_ipo_render(app):
    dt = EquityCalendarTab(
        fetch=lambda f, to: [DividendEvent("KO", "2026-06-02", "2026-07-01", 0.485, 2.9,
                                           "Quarterly", name="Coca-Cola Co")],
        **_dividends_cfg())
    dt.load()
    assert dt.visible_event_count() == 1
    row = dt._tree.topLevelItem(0).child(0)
    # cols: Symbol, Company, Ex-date, Pay date, Amount($), Yield(%), Freq
    assert row.text(0) == "KO" and row.text(1) == "Coca-Cola Co"
    assert "$" in row.text(4) and "%" in row.text(5)

    it = EquityCalendarTab(
        fetch=lambda f, to: [IpoEvent("2026-06-05", "WHK", "WhiteHawk", "NYSE", "25-27", 6.9e6, "expected")],
        **_ipo_cfg())
    it.load()
    assert it.visible_event_count() == 1
    assert it._tree.topLevelItem(0).child(0).text(0) == "WHK"


def test_calendar_space_has_four_pages(app):
    space = CalendarSpace()       # no keys -> equity fetches return [] (graceful)
    names = [n for n, _p in space._pages]
    assert names == ["Economic", "Earnings", "Dividends", "IPO"]
    space.set_page(2)
    assert space._stack.currentIndex() == 2 and space._pills[2].isChecked()
