# src/vike_trader_app/data/calendar/providers/census.py
"""US Census actuals: retail sales, housing starts/permits. Needs free CENSUS_API_KEY."""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = ("https://api.census.gov/data/timeseries/eits/{program}"
       "?get=cell_value,time_slot_id&key={key}&category_code={cat}")
# normalized title → (program, category_code, unit)
SERIES: dict[str, tuple[str, str, str]] = {
    "retail sales": ("marts", "44000", "%"),
    "building permits": ("ressales", "PERMIT", "K"),
}


class CensusProvider:
    name = "Census"

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("CENSUS_API_KEY")
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        if not self._key:
            return {}
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            program, cat, unit = mapped
            try:
                rows = self._http(URL.format(program=program, key=self._key, cat=cat))
                # rows[0] is the header; rows[-1] the latest data row, col 0 = cell_value
                if len(rows) > 1:
                    out[ev.id] = ActualValue(float(rows[-1][0]), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
