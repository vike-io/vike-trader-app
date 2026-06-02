"""ForexFactory weekly JSON schedule provider.

Fetches the publisher's own static weekly files (no API key). Gives every country's
time/importance/forecast/previous; `actual` is NOT in the feed (backfill layer fills it).
"""
from __future__ import annotations

import time

from ..http import http_get_json
from ..model import (
    CalendarEvent, impact_to_importance, iso_to_ts_utc, parse_value, week_start_utc,
)
from ..taxonomy import categorize, currency_country

THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

_WEEK_MS = 7 * 24 * 3600 * 1000


class ForexFactoryProvider:
    name = "ForexFactory"

    def __init__(self, http=http_get_json, *, now_ms=None,
                 this_week_url: str = THIS_WEEK, next_week_url: str = NEXT_WEEK):
        self._http = http
        self._now = now_ms if now_ms is not None else (lambda: int(time.time() * 1000))
        self._this_url = this_week_url
        self._next_url = next_week_url

    def fetch_week(self, week_start_ms: int) -> list[CalendarEvent]:
        # ForexFactory publishes ONLY the current and next week as static files. For any
        # other week there is no file, so return nothing (the calendar shows "no data")
        # rather than mis-filing the current week's events under the requested week.
        this_week = week_start_utc(self._now())
        if week_start_ms == this_week:
            url = self._this_url
        elif week_start_ms == this_week + _WEEK_MS:
            url = self._next_url
        else:
            return []
        records = self._http(url)
        return [self._to_event(r) for r in records]

    @staticmethod
    def _to_event(r: dict) -> CalendarEvent:
        currency = r.get("country", "")            # ForexFactory puts the code in `country`
        country, _iso = currency_country(currency)
        raw_date = r.get("date", "")
        ts = iso_to_ts_utc(raw_date)
        is_holiday = r.get("impact") == "Holiday"
        all_day = is_holiday and "T00:00:00" in raw_date
        # ForexFactory feed is published in GMT+3; we only key off the local midnight + Holiday flag.
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
