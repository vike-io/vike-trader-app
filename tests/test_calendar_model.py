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
