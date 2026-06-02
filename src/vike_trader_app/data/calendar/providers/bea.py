# src/vike_trader_app/data/calendar/providers/bea.py
"""BEA (US Bureau of Economic Analysis) actuals: GDP, PCE. Needs free BEA_API_KEY."""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = ("https://apps.bea.gov/api/data?UserID={key}&method=GetData&ResultFormat=JSON"
       "&datasetname=NIPA&TableName={table}&Frequency=Q&Year=LAST5")
# Keyed by normalized ForexFactory USD titles ("Advance/Prelim/Final GDP q/q" all normalize
# to "gdp q/q"). T10101 is "Percent Change From Preceding Period in Real GDP" (the reported
# annualized rate). NEEDS a free BEA_API_KEY — unverified here (no key on hand) and redundant
# with FRED's GDP (which runs first), so this is a fallback only.
TABLES: dict[str, tuple[str, str]] = {
    "gdp q/q": ("T10101", "%"),
}


class BeaProvider:
    name = "BEA"

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("BEA_API_KEY")
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        if not self._key:
            return {}
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = TABLES.get(normalize_title(ev.title))
            if not mapped:
                continue
            table, unit = mapped
            try:
                data = self._http(URL.format(key=self._key, table=table))
                rows = data["BEAAPI"]["Results"]["Data"]
                if rows:
                    out[ev.id] = ActualValue(float(rows[-1]["DataValue"].replace(",", "")),
                                             unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
