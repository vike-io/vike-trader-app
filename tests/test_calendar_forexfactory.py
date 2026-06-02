from vike_trader_app.data.calendar.providers.base import ScheduleProvider, ActualsProvider
from vike_trader_app.data.calendar import http


def test_base_protocols_importable():
    assert hasattr(ScheduleProvider, "fetch_week")
    assert hasattr(ActualsProvider, "backfill")


def test_http_module_exposes_getters():
    assert callable(http.http_get_json)
    assert callable(http.http_get_text)
