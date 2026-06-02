# tests/test_economic_calendar_gui.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")

from datetime import timezone  # noqa: E402

from PySide6 import QtWidgets, QtGui
from vike_trader_app.ui.calendar_delegate import importance_bar_pixmap, value_color


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_importance_pixmap_sizes(app):
    pm = importance_bar_pixmap(2)
    assert isinstance(pm, QtGui.QPixmap) and not pm.isNull()


def test_value_color_beat_miss(app):
    from vike_trader_app.ui import theme
    assert value_color(actual=3.5, forecast=3.2) == theme.UP     # beat
    assert value_color(actual=3.0, forecast=3.2) == theme.DOWN   # miss
    assert value_color(actual=3.2, forecast=3.2) == theme.TEXT   # inline
    assert value_color(actual=None, forecast=3.2) == theme.TEXT  # unreleased


# ---------------------------------------------------------------------------
# Task 12 — EconomicCalendarTab
# ---------------------------------------------------------------------------
from vike_trader_app.ui.economic_calendar import EconomicCalendarTab  # noqa: E402
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc, week_start_utc  # noqa: E402

TS_TUE = iso_to_ts_utc("2026-06-02T12:30:00+00:00")
TS_WED = iso_to_ts_utc("2026-06-03T08:00:00+00:00")
WK = week_start_utc(TS_TUE)


def _ev(ts, currency, title, importance, actual=None, forecast=None):
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title), ts_utc=ts, all_day=False,
        country={"USD": "United States", "EUR": "European Union"}[currency],
        currency=currency, title=title, category="other", importance=importance,
        actual=actual, forecast=forecast, previous=None, unit="%",
        actual_display=("" if actual is None else f"{actual}%"),
        forecast_display=("" if forecast is None else f"{forecast}%"),
        previous_display="")


class _FakeRepo:
    def __init__(self, evs): self._evs = evs
    def get_week(self, ws, *, force=False): return list(self._evs)


def _drain(tab, app):
    """Wait for an async fetch worker to finish and deliver its queued eventsReady signal."""
    w = getattr(tab, "_worker", None)
    if w is not None:
        w.wait(3000)
    app.processEvents()


def _tab(app):
    repo = _FakeRepo([
        _ev(TS_TUE, "USD", "JOLTs Job Openings", 2, actual=6.82, forecast=6.9),
        _ev(TS_TUE, "EUR", "Inflation Rate YoY", 1, actual=3.2, forecast=3.2),
        _ev(TS_WED, "USD", "GDP Growth Rate QoQ", 2, forecast=0.5),
    ])
    t = EconomicCalendarTab(repository=repo, tz=timezone.utc)  # pin UTC for deterministic times
    t.load_week(WK)
    t.set_now_ms(WK + 8 * 24 * 3600 * 1000)   # now after the week => no "now" marker row
    return t


def test_tree_groups_by_date_then_event(app):
    t = _tab(app)
    # two date-header top-level rows (Tue, Wed)
    roots = [t._tree.topLevelItem(i).text(0) for i in range(t._tree.topLevelItemCount())]
    assert any("June 2" in r for r in roots) and any("June 3" in r for r in roots)


def test_importance_filter_high_only_reduces_rows(app):
    t = _tab(app)
    assert t.visible_event_count() == 3
    t.set_high_only(True)
    assert t.visible_event_count() == 2     # the medium EUR row is hidden


def test_country_filter(app):
    t = _tab(app)
    t.set_countries({"USD"})
    assert t.visible_event_count() == 2     # only US events


def test_countdown_text_for_future_event(app):
    t = _tab(app)
    # pin "now" 90 minutes before the Wednesday GDP event
    t.set_now_ms(TS_WED - 90 * 60_000)
    assert t.countdown_text(TS_WED) == "Coming in 1:30:00"


# ---------------------------------------------------------------------------
# Task 13 — toolbar + week strip
# ---------------------------------------------------------------------------
def test_week_nav_changes_week_and_reloads(app):
    t = _tab(app)
    start = t.current_week_start()
    t.go_next_week()
    _drain(t, app)
    assert t.current_week_start() == start + 7 * 24 * 3600 * 1000
    t.go_today()
    _drain(t, app)
    assert t.current_week_start() == week_start_utc(t._now())


def test_week_strip_has_seven_day_cards(app):
    t = _tab(app)
    assert t.day_card_count() == 7


def test_category_filter(app):
    t = _tab(app)                  # GDP event has category "other" in the fixture builder
    t.set_category("inflation")
    # only events categorized inflation remain; fixture builder uses "other", so expect 0
    assert t.visible_event_count() == 0
    t.set_category("All")
    assert t.visible_event_count() == 3


# ---------------------------------------------------------------------------
# Task 14 — background fetch worker + live countdown timer
# ---------------------------------------------------------------------------
from vike_trader_app.ui.economic_calendar import _CalendarFetchWorker  # noqa: E402


def test_fetch_worker_emits_events(app, qtbot=None):
    repo = _FakeRepo([_ev(TS_TUE, "USD", "CPI", 2, actual=3.2, forecast=3.1)])
    worker = _CalendarFetchWorker(repo, WK)
    got = {}
    worker.eventsReady.connect(lambda evs: got.setdefault("evs", evs))
    worker.run()                      # call run() directly (no thread) for a deterministic test
    assert got["evs"][0].title == "CPI"


def test_tick_refreshes_only_future_countdowns(app):
    t = _tab(app)
    t.set_now_ms(TS_WED - 2 * 60_000)         # 2 minutes before GDP
    assert t.countdown_text(TS_WED) == "Coming in 0:02:00"
    t.set_now_ms(TS_WED - 60_000)
    t._tick()                                  # advance; should not raise, recomputes labels
    assert t.countdown_text(TS_WED) == "Coming in 0:01:00"


# ---------------------------------------------------------------------------
# Task 15 — expandable per-event detail row
# ---------------------------------------------------------------------------
def test_clicking_event_toggles_detail_child(app):
    t = _tab(app)
    top = t._tree.topLevelItem(0)
    row = top.child(0)
    assert row.childCount() == 0
    t._toggle_detail(row)
    assert row.childCount() == 1            # detail node added
    assert "Forecast" in row.child(0).text(0) or row.child(0).text(0) != ""
    t._toggle_detail(row)
    assert row.childCount() == 0            # collapses again


# ---------------------------------------------------------------------------
# Task 16 — MainWindow rail wiring
# ---------------------------------------------------------------------------
def test_mainwindow_registers_calendar_rail_item():
    # Class-attribute check — no MainWindow construction (which loads symbols and can be
    # flaky offscreen). Verifies the rail wiring; the actual addTab is checked manually
    # (Task 18) and guarded by the rail-count == tab-count invariant in the app.
    from vike_trader_app.ui.app import MainWindow
    assert ("▦", "Calendar") in MainWindow._RAIL_ITEMS


# ---------------------------------------------------------------------------
# Task 17 — flag/ISO country chips
# ---------------------------------------------------------------------------
def test_country_cell_shows_iso_chip_when_no_flag_asset(app):
    from vike_trader_app.ui.economic_calendar import country_chip_pixmap
    pm = country_chip_pixmap("us")
    assert not pm.isNull()
    pm2 = country_chip_pixmap("")          # unknown → still returns a (blank) pixmap, no crash
    assert pm2 is not None


# ---------------------------------------------------------------------------
# Task 18 — showEvent loads the current week exactly once
# ---------------------------------------------------------------------------
def test_show_event_loads_week_once(app):
    repo = _FakeRepo([_ev(TS_TUE, "USD", "CPI", 2, actual=3.2, forecast=3.1)])
    t = EconomicCalendarTab(repository=repo)
    assert t.visible_event_count() == 0          # nothing loaded before shown
    from PySide6 import QtGui
    t.showEvent(QtGui.QShowEvent())
    _drain(t, app)
    assert t.visible_event_count() == 1          # loaded on first show
    # second show must NOT reload (swap to an empty repo; count stays)
    t._repo = _FakeRepo([])
    t.showEvent(QtGui.QShowEvent())
    assert t.visible_event_count() == 1          # load-once guard held


# ---------------------------------------------------------------------------
# Task 19 — calendar rail icon is registered
# ---------------------------------------------------------------------------
def test_calendar_rail_icon_registered(app):
    from vike_trader_app.ui import icons
    assert "calendar" in icons._DRAW
    pm = icons._pixmap("calendar", "#ffffff")
    assert not pm.isNull()


# ---------------------------------------------------------------------------
# Task 20 — TradingView-exact country/time dedup within a date (once per run)
# ---------------------------------------------------------------------------
def test_country_and_time_dedup_within_date(app):
    # one date: two USD events at the SAME time, then a USD event at a LATER time.
    same = TS_TUE                       # 12:30
    later = TS_TUE + 3600_000           # 13:30, still USD
    repo = _FakeRepo([
        _ev(same, "USD", "AAA Event", 1, forecast=1.0),
        _ev(same, "USD", "BBB Event", 1, forecast=2.0),   # same ts + country as AAA
        _ev(later, "USD", "CCC Event", 1, forecast=3.0),  # later time, same country
    ])
    t = EconomicCalendarTab(repository=repo, tz=timezone.utc)  # pin UTC for deterministic times
    t.load_week(WK)
    t.set_now_ms(later + 3600_000)       # now after all events => no "now" marker shifts indices
    top = t._tree.topLevelItem(0)        # single date header
    rows = [top.child(i) for i in range(top.childCount())]
    # sorted by (ts, country, title): AAA, BBB (same ts), then CCC
    assert rows[0].text(0) == "12:30" and rows[0].text(1) == "United States"   # first of run: shown
    assert rows[1].text(0) == "" and rows[1].text(1) == ""                     # same ts+country: blanked
    assert rows[2].text(0) == "13:30" and rows[2].text(1) == "United States"   # time changed: shown again


# ---------------------------------------------------------------------------
# Task 21 — empty week shows a "no data" hint (ForexFactory covers this/next week)
# ---------------------------------------------------------------------------
def test_empty_week_shows_no_data_hint(app):
    t = EconomicCalendarTab(repository=_FakeRepo([]))
    t.load_week(WK)
    assert t.visible_event_count() == 0
    assert "no data" in t._status.text().lower()


def test_non_empty_week_clears_status(app):
    t = _tab(app)   # _tab loads 3 events
    assert t._status.text() == ""


# ---------------------------------------------------------------------------
# Task 22 — async week-nav: immediate header update + latest-nav-wins (QA fixes)
# ---------------------------------------------------------------------------
def test_nav_updates_range_label_immediately(app):
    t = _tab(app)
    before = t._lbl_range.text()
    t.go_next_week()                       # refresh_async -> _show_loading updates label NOW
    assert t._lbl_range.text() != before   # advanced synchronously, before the async load
    assert t._status.text() == "Loading…"
    _drain(t, app)                         # let the worker settle so teardown is clean


def test_stale_worker_result_is_ignored(app):
    t = _tab(app)                          # loaded current week (WK), 3 events
    t._loading_week = WK - 7 * 24 * 3600 * 1000   # pretend a worker is loading an OLD week
    t._events = []
    t._on_events([_ev(TS_TUE, "USD", "Stale", 2)])   # result for a week != current
    assert t._events == []                 # stale result dropped (latest-nav-wins)


def test_matching_worker_result_is_applied(app):
    t = _tab(app)
    t._loading_week = t._week_start
    t._on_events([_ev(TS_TUE, "USD", "Fresh", 2)])
    assert len(t._events) == 1 and t._events[0].title == "Fresh"


def test_stop_workers_runs_cleanly(app):
    t = _tab(app)
    t.refresh_async()
    t._stop_workers()                      # must not raise; waits any running fetch
    _drain(t, app)


# ---------------------------------------------------------------------------
# Task 23 — parity: country selector, timezone selector, now marker
# ---------------------------------------------------------------------------
def test_country_button_filter(app):
    t = _tab(app)                          # 2 USD + 1 EUR events
    assert t.visible_event_count() == 3
    t._apply_countries({"USD"})
    assert t.visible_event_count() == 2
    t._apply_countries(None)               # "All countries"
    assert t.visible_event_count() == 3


def test_set_timezone_shifts_displayed_time(app):
    from datetime import timedelta
    repo = _FakeRepo([_ev(TS_TUE, "USD", "X", 2, forecast=1.0)])
    t = EconomicCalendarTab(repository=repo, tz=timezone.utc)
    t.load_week(WK)
    t.set_now_ms(TS_TUE + 7200_000)        # after the event => no marker
    assert t._tree.topLevelItem(0).child(0).text(0) == "12:30"
    t.set_timezone(timezone(timedelta(hours=3)))
    assert t._tree.topLevelItem(0).child(0).text(0) == "15:30"


def test_now_marker_inserted_within_week(app):
    repo = _FakeRepo([
        _ev(TS_TUE, "USD", "Past", 2, actual=1.0, forecast=1.0),       # 12:30
        _ev(TS_TUE + 7200_000, "USD", "Future", 2, forecast=2.0),      # 14:30
    ])
    t = EconomicCalendarTab(repository=repo, tz=timezone.utc)
    t.load_week(WK)
    t.set_now_ms(TS_TUE + 3600_000)        # 13:30 — between the two events
    top = t._tree.topLevelItem(0)
    texts = [top.child(i).text(0) for i in range(top.childCount())]
    assert any("now" in x for x in texts)            # marker present
    assert t.visible_event_count() == 2              # marker not counted as an event
