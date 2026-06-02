# tests/test_calendar_actuals_more.py
from vike_trader_app.data.calendar.providers.bls import BlsProvider
from vike_trader_app.data.calendar.providers.bea import BeaProvider
from vike_trader_app.data.calendar.providers.census import CensusProvider
from vike_trader_app.data.calendar.providers.ecb import EcbProvider
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(title, currency):
    ts = iso_to_ts_utc("2026-06-05T12:30:00+00:00")
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, currency, title), ts_utc=ts, all_day=False,
        country="x", currency=currency, title=title, category="inflation",
        importance=2, actual=None, forecast=None, previous=None, unit="",
        actual_display="", forecast_display="", previous_display="")


def test_bls_backfills_cpi():
    fake = {"Results": {"series": [{"data": [{"value": "317.6"}]}]}}
    p = BlsProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Inflation Rate YoY", "USD")
    out = p.backfill([ev])
    assert out[ev.id].value == 317.6 and out[ev.id].source == "BLS"


def test_bea_backfills_gdp():
    fake = {"BEAAPI": {"Results": {"Data": [{"DataValue": "2.7"}]}}}
    p = BeaProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("GDP Growth Rate QoQ", "USD")
    assert p.backfill([ev])[ev.id].value == 2.7


def test_census_backfills_retail():
    fake = [["cell_value", "time"], ["712345", "2026-05"]]
    p = CensusProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Retail Sales MoM", "USD")
    assert p.backfill([ev])[ev.id].source == "Census"


def test_ecb_needs_no_key_and_backfills_eu():
    fake = {"dataSets": [{"series": {"0:0:0": {"observations": {"0": [2.5]}}}}]}
    p = EcbProvider(http=lambda url, **kw: fake)
    ev = _ev("Inflation Rate YoY Flash", "EUR")
    out = p.backfill([ev])
    assert out[ev.id].value == 2.5 and out[ev.id].source == "ECB"


def test_all_skip_unmapped():
    for P in (BlsProvider(api_key="k", http=lambda u, **k: {}),
              BeaProvider(api_key="k", http=lambda u, **k: {}),
              CensusProvider(api_key="k", http=lambda u, **k: []),
              EcbProvider(http=lambda u, **k: {})):
        assert P.backfill([_ev("Totally Unknown Event", "USD")]) == {}
