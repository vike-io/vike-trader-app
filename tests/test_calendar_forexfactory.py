from vike_trader_app.data.calendar.providers.base import ScheduleProvider, ActualsProvider
from vike_trader_app.data.calendar import http


def test_base_protocols_importable():
    assert hasattr(ScheduleProvider, "fetch_week")
    assert hasattr(ActualsProvider, "backfill")


def test_http_module_exposes_getters():
    assert callable(http.http_get_json)
    assert callable(http.http_get_text)


# tests/test_calendar_forexfactory.py  (append)
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from vike_trader_app.data.calendar.providers.forexfactory import ForexFactoryProvider  # noqa: E402
from vike_trader_app.data.calendar.model import week_start_utc, iso_to_ts_utc  # noqa: E402

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ff_calendar_thisweek.json").read_text("utf-8"))

# the fixture's events live in the week of Mon 1 Jun 2026; pin "now" inside it so that
# week is treated as "this week" by the week-aware provider.
NOW = iso_to_ts_utc("2026-06-02T12:00:00+00:00")
THIS = week_start_utc(NOW)
_WEEK = 7 * 24 * 3600 * 1000


def _provider(next_recs=None):
    # fake http: thisweek URL -> FIXTURE, nextweek URL -> next_recs (or empty)
    def http(url, **kw):
        if "nextweek" in url:
            return next_recs if next_recs is not None else []
        return FIXTURE
    return ForexFactoryProvider(http=http, now_ms=lambda: NOW)


def test_parses_all_records_into_events():
    evs = _provider().fetch_week(THIS)
    assert len(evs) == 4


def test_maps_fields_units_and_importance():
    evs = {e.title: e for e in _provider().fetch_week(THIS)}
    nfp = evs["Non-Farm Payrolls"]
    assert nfp.currency == "USD" and nfp.country == "United States"
    assert nfp.importance == 2 and nfp.category == "employment"
    assert nfp.forecast == 185.0 and nfp.unit == "K"
    ca = evs["Current Account"]
    assert ca.previous == -23.0 and ca.unit == "B A$"
    assert ca.forecast is None and ca.forecast_display == ""


def test_actual_is_blank_from_schedule_only():
    evs = _provider().fetch_week(THIS)
    assert all(e.actual is None and e.actual_display == "" for e in evs)


def test_id_is_deterministic_across_fetches():
    a = {e.id for e in _provider().fetch_week(THIS)}
    b = {e.id for e in _provider().fetch_week(THIS)}
    assert a == b and len(a) == 4


def test_next_week_uses_next_url():
    next_fix = [{"title": "Future Event", "country": "USD",
                 "date": "2026-06-09T12:00:00+03:00", "impact": "Low",
                 "forecast": "", "previous": ""}]
    evs = _provider(next_recs=next_fix).fetch_week(THIS + _WEEK)
    assert [e.title for e in evs] == ["Future Event"]


def test_out_of_range_week_returns_empty():
    # past week and far-future week have no published ForexFactory file
    p = _provider()
    assert p.fetch_week(THIS - _WEEK) == []
    assert p.fetch_week(THIS + 2 * _WEEK) == []
