from vike_trader_app.data.calendar.model import CalendarEvent, ActualValue


def test_make_id_is_stable_and_distinct():
    a = CalendarEvent.make_id(1_700_000_000_000, "USD", "Non-Farm Payrolls")
    b = CalendarEvent.make_id(1_700_000_000_000, "USD", "Non-Farm Payrolls")
    c = CalendarEvent.make_id(1_700_000_000_000, "USD", "CPI")
    assert a == b and a != c and isinstance(a, str)


def test_event_roundtrips_through_dict():
    ev = CalendarEvent(
        id="x", ts_utc=1_700_000_000_000, all_day=False, country="US",
        currency="USD", title="CPI", category="inflation", importance=2,
        actual=3.2, forecast=3.2, previous=3.0, unit="%",
        actual_display="3.2%", forecast_display="3.2%", previous_display="3%",
        actual_source="BLS",
    )
    assert CalendarEvent.from_dict(ev.to_dict()) == ev


def test_actual_value_holds_number_unit_source():
    av = ActualValue(value=6.82, unit="M", source="FRED")
    assert (av.value, av.unit, av.source) == (6.82, "M", "FRED")


# tests/test_calendar_model.py  (append)
import pytest
from vike_trader_app.data.calendar.model import (
    parse_value, impact_to_importance, iso_to_ts_utc, week_start_utc,
)


@pytest.mark.parametrize("raw, value, unit", [
    ("3.2%", 3.2, "%"),
    ("−27.1 B A$", -27.1, "B A$"),   # unicode minus
    ("-27.1B A$", -27.1, "B A$"),    # ascii minus, no space
    ("65.94 K", 65.94, "K"),
    ("6.82 M", 6.82, "M"),
    ("103.15", 103.15, ""),
    ("", None, ""),
    ("—", None, ""),                 # em dash = no value
])
def test_parse_value(raw, value, unit):
    assert parse_value(raw) == (value, unit)


def test_impact_to_importance():
    assert impact_to_importance("High") == 2
    assert impact_to_importance("Medium") == 1
    assert impact_to_importance("Low") == 0
    assert impact_to_importance("Holiday") == 0
    assert impact_to_importance("anything else") == 0


def test_iso_to_ts_utc_handles_offset():
    # 2026-06-02T12:30:00+03:00 == 09:30:00Z
    assert iso_to_ts_utc("2026-06-02T12:30:00+03:00") == 1_780_392_600_000


def test_week_start_utc_is_monday_midnight():
    # a Tuesday → Monday 00:00:00Z of that ISO week
    tue = iso_to_ts_utc("2026-06-02T12:30:00+00:00")
    mon = iso_to_ts_utc("2026-06-01T00:00:00+00:00")
    assert week_start_utc(tue) == mon
