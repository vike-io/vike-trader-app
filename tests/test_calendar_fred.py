# tests/test_calendar_fred.py
from vike_trader_app.data.calendar.providers.fred import FredProvider
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(title, currency="USD"):
    ts = iso_to_ts_utc("2026-06-05T12:30:00+00:00")
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title), ts_utc=ts, all_day=False,
        country="United States", currency=currency, title=title, category="employment",
        importance=2, actual=None, forecast=185.0, previous=177.0, unit="K",
        actual_display="", forecast_display="185K", previous_display="177K")


def test_no_key_returns_empty():
    p = FredProvider(api_key=None, http=lambda url, **kw: {})
    assert p.backfill([_ev("Non-Farm Payrolls")]) == {}


def test_backfills_mapped_us_event():
    # FRED series/observations JSON shape
    fake = {"observations": [{"date": "2026-06-01", "value": "272.4"}]}
    p = FredProvider(api_key="k", http=lambda url, **kw: fake)
    out = p.backfill([_ev("Non-Farm Payrolls")])
    ev_id = _ev("Non-Farm Payrolls").id
    assert ev_id in out and out[ev_id].value == 272.4 and out[ev_id].source == "FRED"


def test_ignores_unmapped_or_nonus_events():
    p = FredProvider(api_key="k", http=lambda url, **kw: {"observations": []})
    assert p.backfill([_ev("Mystery Indicator")]) == {}
    assert p.backfill([_ev("Non-Farm Payrolls", currency="EUR")]) == {}
