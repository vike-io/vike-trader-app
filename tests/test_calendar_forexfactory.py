from vike_trader_app.data.calendar.providers.base import ScheduleProvider, ActualsProvider
from vike_trader_app.data.calendar import http


def test_base_protocols_importable():
    assert hasattr(ScheduleProvider, "fetch_week")
    assert hasattr(ActualsProvider, "backfill")


def test_http_module_exposes_getters():
    assert callable(http.http_get_json)
    assert callable(http.http_get_text)


# tests/test_calendar_forexfactory.py  (append)
import json
from pathlib import Path

from vike_trader_app.data.calendar.providers.forexfactory import ForexFactoryProvider
from vike_trader_app.data.calendar.model import week_start_utc, iso_to_ts_utc

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ff_calendar_thisweek.json").read_text("utf-8"))


def _provider():
    # inject http that ignores the URL and returns the fixture
    return ForexFactoryProvider(http=lambda url, **kw: FIXTURE)


def test_parses_all_records_into_events():
    evs = _provider().fetch_week(week_start_utc(iso_to_ts_utc("2026-06-02T00:00:00+00:00")))
    assert len(evs) == 4


def test_maps_fields_units_and_importance():
    evs = {e.title: e for e in _provider().fetch_week(0)}
    nfp = evs["Non-Farm Payrolls"]
    assert nfp.currency == "USD" and nfp.country == "United States"
    assert nfp.importance == 2 and nfp.category == "employment"
    assert nfp.forecast == 185.0 and nfp.unit == "K"
    ca = evs["Current Account"]
    assert ca.previous == -23.0 and ca.unit == "B A$"
    assert ca.forecast is None and ca.forecast_display == ""


def test_actual_is_blank_from_schedule_only():
    evs = _provider().fetch_week(0)
    assert all(e.actual is None and e.actual_display == "" for e in evs)


def test_id_is_deterministic_across_fetches():
    a = {e.id for e in _provider().fetch_week(0)}
    b = {e.id for e in _provider().fetch_week(0)}
    assert a == b and len(a) == 4
