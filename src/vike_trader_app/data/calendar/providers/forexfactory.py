"""ForexFactory weekly JSON schedule provider.

Fetches the publisher's own static weekly files (no API key). Gives every country's
time/importance/forecast/previous; `actual` is NOT in the feed (backfill layer fills it).
"""
from __future__ import annotations

from ..http import http_get_json
from ..model import (
    CalendarEvent, impact_to_importance, iso_to_ts_utc, parse_value,
)
from ..taxonomy import categorize, currency_country

THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"


class ForexFactoryProvider:
    name = "ForexFactory"

    def __init__(self, http=http_get_json, *, url: str = THIS_WEEK):
        self._http = http
        self._url = url

    def fetch_week(self, week_start_utc: int) -> list[CalendarEvent]:
        records = self._http(self._url)
        return [self._to_event(r) for r in records]

    @staticmethod
    def _to_event(r: dict) -> CalendarEvent:
        currency = r.get("country", "")            # ForexFactory puts the code in `country`
        country, _iso = currency_country(currency)
        ts = iso_to_ts_utc(r["date"])
        all_day = r["date"].endswith("00:00:00+03:00") and r.get("impact") == "Holiday"
        fval, funit = parse_value(r.get("forecast", ""))
        pval, punit = parse_value(r.get("previous", ""))
        title = r.get("title", "")
        return CalendarEvent(
            id=CalendarEvent.make_id(ts, currency, title),
            ts_utc=ts, all_day=all_day, country=country, currency=currency,
            title=title, category=categorize(title),
            importance=impact_to_importance(r.get("impact", "")),
            actual=None, forecast=fval, previous=pval,
            unit=funit or punit,
            actual_display="",
            forecast_display=(r.get("forecast") or "").replace("−", "-"),
            previous_display=(r.get("previous") or "").replace("−", "-"),
        )
