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


def test_bls_backfills_unemployment():
    # ForexFactory "Unemployment Rate"; BLS returns the rate itself (%)
    fake = {"Results": {"series": [{"data": [{"value": "4.3"}]}]}}
    p = BlsProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Unemployment Rate", "USD")
    out = p.backfill([ev])
    assert out[ev.id].value == 4.3 and out[ev.id].source == "BLS"


def test_bea_backfills_gdp():
    fake = {"BEAAPI": {"Results": {"Data": [{"DataValue": "2.7"}]}}}
    p = BeaProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Advance GDP q/q", "USD")   # normalizes to "gdp q/q"
    assert p.backfill([ev])[ev.id].value == 2.7


def test_census_backfills_retail():
    # EITS rows: header + data; pick MPCSM / 44X72 / SA, latest by time. 0.53 -> 0.5
    fake = [
        ["cell_value", "category_code", "seasonally_adj", "data_type_code", "time"],
        ["0.20", "44X72", "yes", "MPCSM", "2026-03"],
        ["0.53", "44X72", "yes", "MPCSM", "2026-04"],
        ["757085", "44X72", "yes", "SM", "2026-04"],   # level row must be ignored
    ]
    p = CensusProvider(api_key="k", http=lambda url, **kw: fake)
    ev = _ev("Retail Sales m/m", "USD")
    out = p.backfill([ev])
    assert out[ev.id].value == 0.5 and out[ev.id].source == "Census"


def test_ecb_needs_no_key_and_backfills_eu():
    fake = {"dataSets": [{"series": {"0:0:0": {"observations": {"0": [2.53]}}}}]}
    p = EcbProvider(http=lambda url, **kw: fake)
    ev = _ev("CPI Estimate y/y", "EUR")   # ForexFactory's euro-area HICP title
    out = p.backfill([ev])
    assert out[ev.id].value == 2.5 and out[ev.id].source == "ECB"   # rounded to one decimal


def test_all_skip_unmapped():
    for P in (BlsProvider(api_key="k", http=lambda u, **k: {}),
              BeaProvider(api_key="k", http=lambda u, **k: {}),
              CensusProvider(api_key="k", http=lambda u, **k: []),
              EcbProvider(http=lambda u, **k: {})):
        assert P.backfill([_ev("Totally Unknown Event", "USD")]) == {}
