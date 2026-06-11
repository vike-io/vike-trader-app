"""Comprehensive "real user" simulation for the Calendar space.

Drives the actual widgets a user touches — the CalendarSpace sub-tab bar over the four
calendars (Economic / Earnings / Dividends / IPO) plus the standalone tabs — the way a click
would: inject events through each tab's own data seam (an injectable repository for Economic, an
injectable ``fetch(from, to)`` for the equity tabs), switch pages via the pill bar, populate and
assert on every table, apply the real filters (importance / country / category / symbol / covered /
timezone), and select a row. No network, no modals, everything on the main thread.

Data-injection patterns are borrowed straight from the existing offline tests:
  * tests/gui/panels/test_economic_calendar_gui.py — ``_FakeRepo`` + ``CalendarEvent`` builder.
  * tests/gui/panels/test_equity_calendar_gui.py — ``EquityCalendarTab(fetch=..., **_cfg())`` + load().
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from datetime import timedelta, timezone  # noqa: E402

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.data.calendar.equity import (  # noqa: E402
    DividendEvent,
    EarningsEvent,
    IpoEvent,
)
from vike_trader_app.data.calendar.model import (  # noqa: E402
    CalendarEvent,
    iso_to_ts_utc,
    week_start_utc,
)
from vike_trader_app.ui.economic_calendar import EconomicCalendarTab  # noqa: E402
from vike_trader_app.ui.equity_calendar import (  # noqa: E402
    CalendarSpace,
    EquityCalendarTab,
    _dividends_cfg,
    _earnings_cfg,
    _ipo_cfg,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- the week we pin every calendar to (Mon Jun 1 .. Sun Jun 7, 2026, UTC) -----------------
TS_MON = iso_to_ts_utc("2026-06-01T08:00:00+00:00")
TS_TUE = iso_to_ts_utc("2026-06-02T12:30:00+00:00")
TS_WED = iso_to_ts_utc("2026-06-03T08:00:00+00:00")
WK = week_start_utc(TS_TUE)
# a "now" well after the displayed week so no red "now" marker row shifts child indices
AFTER_WEEK = WK + 8 * 24 * 3600 * 1000


def _eco_ev(ts, currency, title, importance, category="other", actual=None, forecast=None):
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title),
        ts_utc=ts,
        all_day=False,
        country={"USD": "United States", "EUR": "European Union", "GBP": "United Kingdom"}[currency],
        currency=currency,
        title=title,
        category=category,
        importance=importance,
        actual=actual,
        forecast=forecast,
        previous=None,
        unit="%",
        actual_display=("" if actual is None else f"{actual}%"),
        forecast_display=("" if forecast is None else f"{forecast}%"),
        previous_display="",
    )


class _FakeRepo:
    """Same seam the economic GUI tests use: get_week(week_start, force=) -> list[CalendarEvent]."""

    def __init__(self, evs):
        self._evs = evs

    def get_week(self, ws, *, force=False):
        return list(self._evs)


def _economic_fixture():
    """An EconomicCalendarTab pinned to UTC + WK with three macro events, loaded synchronously."""
    repo = _FakeRepo([
        _eco_ev(TS_TUE, "USD", "JOLTs Job Openings", 2, "employment", actual=6.82, forecast=6.9),
        _eco_ev(TS_TUE, "EUR", "Inflation Rate YoY", 1, "inflation", actual=3.2, forecast=3.2),
        _eco_ev(TS_WED, "USD", "GDP Growth Rate QoQ", 2, "gdp", forecast=0.5),
    ])
    t = EconomicCalendarTab(repository=repo, tz=timezone.utc)
    t.load_week(WK)
    t.set_now_ms(AFTER_WEEK)
    return t


# --- equity fixtures -----------------------------------------------------------------------
_EARN = [
    EarningsEvent("2026-06-03", "AAPL", "amc", 1.5, 1.62, 9e10, 9.4e10, name="Apple Inc", market_cap=3_500_000.0),
    EarningsEvent("2026-06-03", "MSFT", "bmo", 2.9, 2.85, 6e10, 5.9e10, name="Microsoft Corp", market_cap=3_100_000.0),
    EarningsEvent("2026-06-04", "TSLA", "amc", 0.8, 0.95, 2e10, 2.1e10, name="Tesla Inc", market_cap=900_000.0),
    # uncovered (no estimate) -> hidden by the default "Covered only" toggle
    EarningsEvent("2026-06-04", "ZZZZ", "", None, None, None, None),
]
_DIV = [
    DividendEvent("KO", "2026-06-02", "2026-07-01", 0.485, 2.9, "Quarterly", name="Coca-Cola Co"),
    DividendEvent("JNJ", "2026-06-03", "2026-07-08", 1.24, 3.1, "Quarterly", name="Johnson & Johnson"),
]
_IPO = [
    IpoEvent("2026-06-05", "WHK", "WhiteHawk Income Corp", "NYSE", "25-27", 6.9e6, "upcoming"),
    IpoEvent("2026-06-02", "BIDWU", "Tribeca Strategic", "NASDAQ", "10.00", 14e6, "priced"),
]


def _equity_tab(events, cfg):
    """A standalone EquityCalendarTab fed offline via its injectable fetch, loaded synchronously."""
    t = EquityCalendarTab(fetch=lambda f, to: list(events), **cfg)
    t.set_now_ms(WK)          # pin the week so the synchronous load() reads our window
    t.load()
    return t


def _space():
    """A real CalendarSpace with an injected economic tab; equity tabs are re-pointed at our
    offline fixtures and loaded synchronously (no live fetch / no key needed)."""
    space = CalendarSpace(economic_tab=_economic_fixture())
    # swap each equity tab's fetch for our offline data and load it on the main thread
    for tab, events in ((space.earnings, _EARN), (space.dividends, _DIV), (space.ipo, _IPO)):
        tab._fetch = lambda f, to, _e=events: list(_e)
        tab.set_now_ms(WK)
        tab.load()
    space.refresh_day_counts()
    return space


def _child_count(tab):
    return tab.visible_event_count()


# ===========================================================================================
# 1) The space wires up four pages and starts on Economic
# ===========================================================================================
def test_space_starts_on_economic_with_four_pages(app):
    space = _space()
    names = [n for n, _p in space._pages]
    assert names == ["Economic", "Earnings", "Dividends", "IPO"]
    assert space._stack.currentIndex() == 0
    assert space._pills[0].isChecked()
    # Economic-only filters visible on page 0, equity symbol filter hidden
    assert not space._top_high.isHidden() and not space._top_countries.isHidden()
    assert space._row_search.isHidden()


# ===========================================================================================
# 2) The full multi-tab user journey: click each pill, the right table fills, columns align
# ===========================================================================================
def test_user_walks_every_tab_and_each_table_populates(app):
    space = _space()

    # --- Economic page (already shown) -----------------------------------------------------
    space.set_page(0)
    assert space._stack.currentIndex() == 0
    eco = space.economic
    assert eco.visible_event_count() == 3
    # date-group headers for Tue + Wed are present
    eco_roots = [eco._tree.topLevelItem(i).text(0) for i in range(eco._tree.topLevelItemCount())]
    assert any("June 2" in r for r in eco_roots) and any("June 3" in r for r in eco_roots)
    # Economic header columns are the TradingView layout (7 data cols + a trailing right-padding spacer)
    eco_hdr = [eco._tree.headerItem().text(c) for c in range(eco._tree.columnCount())]
    assert eco_hdr == ["Time", "Country", "", "Event", "Actual", "Forecast", "Prior", ""]

    # --- Earnings page ---------------------------------------------------------------------
    space.set_page(1)
    assert space._stack.currentIndex() == 1 and space._pills[1].isChecked()
    earn = space.earnings
    # "Covered only" is on by default -> the uncovered ZZZZ row is hidden (3 of 4 shown)
    assert earn.visible_event_count() == 3
    earn_hdr = [earn._tree.headerItem().text(c) for c in range(earn._tree.columnCount())]
    # trailing "" = right-padding spacer (last data column is right-aligned numeric Mkt cap)
    assert earn_hdr == ["Time", "Symbol", "Company", "EPS est.", "EPS act.", "Surprise", "Mkt cap", ""]
    # biggest market cap first within the date
    first_day = earn._tree.topLevelItem(0)
    assert first_day.child(0).text(1) == "AAPL"   # 3.5T cap sorts above MSFT (3.1T)

    # --- Dividends page --------------------------------------------------------------------
    space.set_page(2)
    assert space._stack.currentIndex() == 2 and space._pills[2].isChecked()
    div = space.dividends
    assert div.visible_event_count() == 2
    div_hdr = [div._tree.headerItem().text(c) for c in range(div._tree.columnCount())]
    assert div_hdr == ["Symbol", "Company", "Ex-date", "Pay date", "Amount", "Yield", "Freq"]
    div_row = div._tree.topLevelItem(0).child(0)
    assert div_row.text(0) in {"KO", "JNJ"}                 # Symbol col
    assert "$" in div_row.text(4) and "%" in div_row.text(5)  # Amount / Yield formatting

    # --- IPO page --------------------------------------------------------------------------
    space.set_page(3)
    assert space._stack.currentIndex() == 3 and space._pills[3].isChecked()
    ipo = space.ipo
    assert ipo.visible_event_count() == 2
    ipo_hdr = [ipo._tree.headerItem().text(c) for c in range(ipo._tree.columnCount())]
    assert ipo_hdr == ["Symbol", "Company", "Exchange", "Price", "Shares", "Status"]
    ipo_syms = {ipo._tree.topLevelItem(i).child(j).text(0)
                for i in range(ipo._tree.topLevelItemCount())
                for j in range(ipo._tree.topLevelItem(i).childCount())}
    assert {"WHK", "BIDWU"} <= ipo_syms


# ===========================================================================================
# 3) Economic filters as a user would drive them (importance / country / category)
# ===========================================================================================
def test_economic_importance_country_and_category_filters(app):
    space = _space()
    space.set_page(0)
    eco = space.economic
    assert eco.visible_event_count() == 3

    # High-only via the shared top checkbox (drives economic.set_high_only through _chk_high)
    space._top_high.setChecked(True)
    assert eco.visible_event_count() == 2          # the medium EUR inflation row drops out
    space._top_high.setChecked(False)
    assert eco.visible_event_count() == 3

    # Country pill -> only United States events remain
    space._top_countries.set_selected({"USD"})
    assert eco._countries == {"USD"}
    assert eco.visible_event_count() == 2
    assert space._top_countries.text() == "Countries (1)"
    space._top_countries.set_selected(set())       # empty == all countries again
    assert eco.visible_event_count() == 3

    # Category dropdown (shared single-select pill) -> only employment events
    space._cat_btn.set_current("employment")
    space._on_category_changed()                   # what selectionChanged drives
    assert eco.visible_event_count() == 1
    space._cat_btn.set_current("All")
    space._on_category_changed()
    assert eco.visible_event_count() == 3


# ===========================================================================================
# 4) Earnings: the shared "Covered only" toggle + symbol filter behave per-tab
# ===========================================================================================
def test_earnings_covered_toggle_and_symbol_filter(app):
    space = _space()
    space.set_page(1)
    earn = space.earnings
    assert earn.visible_event_count() == 3         # ZZZZ hidden by default

    # turning Covered-only OFF reveals the uncovered row
    space._row_covered.setChecked(False)
    assert earn.visible_event_count() == 4
    space._row_covered.setChecked(True)
    assert earn.visible_event_count() == 3

    # the shared symbol filter narrows to one ticker
    space._row_search.setText("aapl")
    assert earn.visible_event_count() == 1
    assert space.earnings._tree.topLevelItem(0).child(0).text(1) == "AAPL"
    space._row_search.setText("")
    assert earn.visible_event_count() == 3


# ===========================================================================================
# 5) Symbol filter follows the user across equity tabs (Earnings -> Dividends)
# ===========================================================================================
def test_symbol_filter_reapplies_on_tab_switch(app):
    space = _space()
    space.set_page(1)
    space._row_search.setText("KO")                # no AAPL/MSFT/TSLA match -> earnings empties
    assert space.earnings.visible_event_count() == 0
    space.set_page(2)                              # switching re-applies the same filter to dividends
    assert space.dividends.visible_event_count() == 1
    assert space.dividends._tree.topLevelItem(0).child(0).text(0) == "KO"


# ===========================================================================================
# 6) Selecting a row sets it as the tree's current item (the user clicks a row)
# ===========================================================================================
def test_user_selects_a_dividend_row(app):
    space = _space()
    space.set_page(2)
    div = space.dividends
    top = div._tree.topLevelItem(0)
    row = top.child(0)
    div._tree.setCurrentItem(row)
    assert div._tree.currentItem() is row
    assert row.parent() is top                      # it's a leaf row under a date header


# ===========================================================================================
# 7) Economic: expand a row's detail child (the user clicks an event to expand it)
# ===========================================================================================
def test_economic_row_expands_detail(app):
    space = _space()
    space.set_page(0)
    eco = space.economic
    top = eco._tree.topLevelItem(0)
    row = top.child(0)
    assert row.childCount() == 0
    eco._toggle_detail(row)
    assert row.childCount() == 1
    detail_text = row.child(0).text(0)
    assert "Forecast" in detail_text
    eco._toggle_detail(row)
    assert row.childCount() == 0                     # collapses again


# ===========================================================================================
# 8) The day-card strip aggregates counts across all four calendars for the week
# ===========================================================================================
def test_day_cards_aggregate_counts_across_calendars(app):
    space = _space()
    assert len(space._day_cards) == 7
    # Tuesday (index 1) has 2 economic (JOLTs+Inflation), 1 dividend (KO), 1 ipo (BIDWU)
    tue = space._day_cards[1]
    assert tue._rows["economic"][1].text() == "2"
    assert tue._rows["dividends"][1].text() == "1"
    assert tue._rows["ipo"][1].text() == "1"
    # Wednesday (index 2) has 1 economic (GDP), 2 earnings (AAPL+MSFT), 1 dividend (JNJ)
    wed = space._day_cards[2]
    assert wed._rows["economic"][1].text() == "1"
    assert wed._rows["earnings"][1].text() == "2"
    assert wed._rows["dividends"][1].text() == "1"
    # the titles read as weekday + day-of-month
    assert "Tue 2" in tue._title.text() and "Wed 3" in wed._title.text()


# ===========================================================================================
# 9) Clicking a day-card jumps to the Economic page scrolled to that day
# ===========================================================================================
def test_clicking_day_card_navigates_to_economic_day(app):
    space = _space()
    space.set_page(1)                                # start on Earnings
    space._on_day_clicked(2)                         # click Wednesday's card
    assert space._stack.currentIndex() == 0          # jumped back to Economic
    assert space._selected_day == 2
    eco = space.economic
    cur = eco._tree.currentItem()
    assert cur is not None and "June 3" in cur.text(0)   # scrolled/selected Wed's group


# ===========================================================================================
# 10) Timezone change shifts displayed times AND re-buckets the day-cards (full TZ round-trip)
# ===========================================================================================
def test_timezone_shift_moves_event_and_day_card(app):
    # A macro print at 23:30Z lands on the NEXT local day under UTC+8.
    ts = iso_to_ts_utc("2026-06-02T23:30:00+00:00")   # Tue 23:30 UTC == Wed 07:30 at UTC+8
    eco = EconomicCalendarTab(repository=_FakeRepo([_eco_ev(ts, "USD", "Late Print", 2)]),
                              tz=timezone.utc)
    eco.load_week(WK)
    eco.set_now_ms(AFTER_WEEK)
    # under UTC it groups on Tuesday and shows 23:30
    assert eco._tree.topLevelItem(0).child(0).text(0) == "23:30"
    assert "June 2" in eco._tree.topLevelItem(0).text(0)

    space = CalendarSpace(economic_tab=eco)
    for tab in (space.earnings, space.dividends, space.ipo):
        tab._fetch = lambda f, to: []
        tab.set_now_ms(WK)
        tab.load()
    space.refresh_day_counts()
    # the print is counted on Tuesday's card under UTC
    assert space._day_cards[1]._rows["economic"][1].text() == "1"

    # user switches the shared tz selector to UTC+8 -> time shifts and the event moves to Wed
    space.economic.set_timezone(timezone(timedelta(hours=8)))
    space.refresh_day_counts()
    assert eco._tree.topLevelItem(0).child(0).text(0) == "07:30"
    assert "June 3" in eco._tree.topLevelItem(0).text(0)
    # now on Wednesday's card (row visible, count "1") ...
    wed_row, wed_num = space._day_cards[2]._rows["economic"]
    assert wed_row.isVisibleTo(wed_row.parentWidget()) and wed_num.text() == "1"
    # ... and no longer on Tuesday's (a zero-count category row is hidden by set_counts)
    tue_row, _tue_num = space._day_cards[1]._rows["economic"]
    assert not tue_row.isVisibleTo(tue_row.parentWidget())


# ===========================================================================================
# 11) Empty week shows the calendars' "no data" hints (a user lands on a sparse week)
# ===========================================================================================
def test_empty_week_shows_no_data_hints(app):
    eco = EconomicCalendarTab(repository=_FakeRepo([]), tz=timezone.utc)
    eco.load_week(WK)
    assert eco.visible_event_count() == 0
    assert "no data" in eco._status.text().lower()

    earn = _equity_tab([], _earnings_cfg())
    assert earn.visible_event_count() == 0
    assert "no events" in earn._status.text().lower()


# ===========================================================================================
# 12) Standalone equity tabs render typed events correctly (Dividends + IPO cell content)
# ===========================================================================================
def test_standalone_dividends_and_ipo_cells(app):
    div = _equity_tab(_DIV, _dividends_cfg())
    assert div.visible_event_count() == 2
    ko = next(div._tree.topLevelItem(i).child(j)
              for i in range(div._tree.topLevelItemCount())
              for j in range(div._tree.topLevelItem(i).childCount())
              if div._tree.topLevelItem(i).child(j).text(0) == "KO")
    assert ko.text(1) == "Coca-Cola Co"
    assert ko.text(4) == "0.48 $"          # _fmt(0.485, " $") -> .2f of the 0.485 float
    assert ko.text(6) == "Quarterly"

    ipo = _equity_tab(_IPO, _ipo_cfg())
    assert ipo.visible_event_count() == 2
    whk = next(ipo._tree.topLevelItem(i).child(j)
               for i in range(ipo._tree.topLevelItemCount())
               for j in range(ipo._tree.topLevelItem(i).childCount())
               if ipo._tree.topLevelItem(i).child(j).text(0) == "WHK")
    assert whk.text(1) == "WhiteHawk Income Corp" and whk.text(2) == "NYSE"
    assert whk.text(5) == "upcoming"       # Status column


# ===========================================================================================
# 13) Right-aligned numeric columns are flagged on the rendered items (TradingView layout)
# ===========================================================================================
def test_numeric_columns_are_right_aligned(app):
    earn = _equity_tab(_EARN, _earnings_cfg())
    row = earn._tree.topLevelItem(0).child(0)
    for col in (3, 4, 5, 6):               # EPS est/act, Surprise, Mkt cap
        align = int(row.textAlignment(col))
        assert align & int(QtCore.Qt.AlignRight)
