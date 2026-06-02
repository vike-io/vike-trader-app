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
    # keyed by ForexFactory's title ("Non-Farm Employment Change", not "Non-Farm Payrolls")
    fake = {"observations": [{"date": "2026-06-01", "value": "272.4"}]}
    p = FredProvider(api_key="k", http=lambda url, **kw: fake)
    out = p.backfill([_ev("Non-Farm Employment Change")])
    ev_id = _ev("Non-Farm Employment Change").id
    assert ev_id in out and out[ev_id].value == 272.4 and out[ev_id].source == "FRED"


def test_uses_forexfactory_title_and_units_transform():
    # CPI m/m must hit the CPI series with a percent-change transform, not the raw level
    seen = {}

    def fake(url, **kw):
        seen["url"] = url
        return {"observations": [{"value": "0.27"}]}

    p = FredProvider(api_key="k", http=fake)
    out = p.backfill([_ev("CPI m/m")])
    ev_id = _ev("CPI m/m").id
    assert "series_id=CPIAUCSL" in seen["url"] and "units=pch" in seen["url"]
    assert out[ev_id].value == 0.3  # rounded to one decimal


def test_ignores_unmapped_or_nonus_events():
    p = FredProvider(api_key="k", http=lambda url, **kw: {"observations": []})
    assert p.backfill([_ev("Mystery Indicator")]) == {}
    assert p.backfill([_ev("Non-Farm Employment Change", currency="EUR")]) == {}
