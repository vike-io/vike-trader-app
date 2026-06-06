# tests/test_calendar_store.py
from vike_trader_app.data.calendar.store import CalendarStore
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(ts, title):
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, "USD", title), ts_utc=ts, all_day=False,
        country="United States", currency="USD", title=title, category="other",
        importance=1, actual=None, forecast=None, previous=None, unit="",
        actual_display="", forecast_display="", previous_display="")


def test_iso_week_key_format():
    ts = iso_to_ts_utc("2026-06-02T00:00:00+00:00")
    assert CalendarStore.iso_week_key(ts) == "2026-W23"


def test_save_and_load_roundtrip(tmp_path):
    store = CalendarStore(str(tmp_path))
    ts = iso_to_ts_utc("2026-06-02T12:00:00+00:00")
    store.save_week("2026-W23", [_ev(ts, "CPI")])
    again = store.load_week("2026-W23")
    assert [e.title for e in again] == ["CPI"]


def test_load_missing_week_returns_empty(tmp_path):
    assert CalendarStore(str(tmp_path)).load_week("1999-W01") == []


def test_meta_tracks_last_fetch(tmp_path):
    store = CalendarStore(str(tmp_path))
    assert store.last_fetch("2026-W23") == 0
    store.mark_fetched("2026-W23", 1_700_000_000_000)
    assert CalendarStore(str(tmp_path)).last_fetch("2026-W23") == 1_700_000_000_000
