import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402
from vike_trader_app.data.calendar.equity import EarningsEvent, DividendEvent, IpoEvent  # noqa: E402
from vike_trader_app.ui.equity_calendar import (  # noqa: E402
    EquityCalendarTab, CalendarSpace, _earnings_cfg, _dividends_cfg, _ipo_cfg, _fmt_big,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _earn_tab(app, events):
    cols, row, date_of = _earnings_cfg()
    t = EquityCalendarTab(fetch=lambda f, to: list(events), columns=cols, row_fn=row, date_of=date_of)
    t.load()
    return t


def test_earnings_groups_by_date_and_renders(app):
    evs = [
        EarningsEvent("2026-06-03", "AAPL", "amc", 1.5, 1.62, 9e10, 9.4e10),
        EarningsEvent("2026-06-03", "MSFT", "bmo", 2.9, None, 6e10, None),
        EarningsEvent("2026-06-04", "TSLA", "amc", 0.8, 0.7, 2e10, 2.1e10),
    ]
    t = _earn_tab(app, evs)
    assert t.visible_event_count() == 3
    roots = [t._tree.topLevelItem(i).text(0) for i in range(t._tree.topLevelItemCount())]
    assert any("June 3" in r for r in roots) and any("June 4" in r for r in roots)
    # first row: time label + symbol + EPS values
    first = t._tree.topLevelItem(0).child(0)
    assert first.text(1) in ("AAPL", "MSFT") and first.text(0) in ("After-hrs", "Pre-mkt")


def test_symbol_filter(app):
    evs = [
        EarningsEvent("2026-06-03", "AAPL", "amc", 1.5, 1.6, None, None),
        EarningsEvent("2026-06-03", "MSFT", "bmo", 2.9, 3.0, None, None),
    ]
    t = _earn_tab(app, evs)
    assert t.visible_event_count() == 2
    t._on_filter("aapl")
    assert t.visible_event_count() == 1


def test_empty_shows_hint(app):
    t = _earn_tab(app, [])
    assert t.visible_event_count() == 0
    assert "no events" in t._status.text().lower()


def test_fmt_big_magnitudes():
    assert _fmt_big(9.4e10) == "94.0B" and _fmt_big(2.1e6) == "2.1M" and _fmt_big(None) == "—"


def test_dividends_and_ipo_cfg_render(app):
    dcols, drow, ddate = _dividends_cfg()
    dt = EquityCalendarTab(
        fetch=lambda f, to: [DividendEvent("KO", "2026-06-02", "2026-07-01", 0.485, 2.9, "Quarterly")],
        columns=dcols, row_fn=drow, date_of=ddate)
    dt.load()
    assert dt.visible_event_count() == 1
    row = dt._tree.topLevelItem(0).child(0)
    assert row.text(0) == "KO" and "$" in row.text(3) and "%" in row.text(4)

    icols, irow, idate = _ipo_cfg()
    it = EquityCalendarTab(
        fetch=lambda f, to: [IpoEvent("2026-06-05", "WHK", "WhiteHawk", "NYSE", "25-27", 6.9e6, "expected")],
        columns=icols, row_fn=irow, date_of=idate)
    it.load()
    assert it.visible_event_count() == 1
    assert it._tree.topLevelItem(0).child(0).text(0) == "WHK"


def test_calendar_space_has_four_pages(app):
    space = CalendarSpace()       # no keys -> equity fetches return [] (graceful)
    names = [n for n, _p in space._pages]
    assert names == ["Economic", "Earnings", "Dividends", "IPO"]
    space.set_page(2)
    assert space._stack.currentIndex() == 2 and space._pills[2].isChecked()
