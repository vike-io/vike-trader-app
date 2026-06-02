# src/vike_trader_app/data/calendar/providers/bea.py
"""BEA (US Bureau of Economic Analysis) actuals: GDP, PCE. Needs free BEA_API_KEY."""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = ("https://apps.bea.gov/api/data?UserID={key}&method=GetData&ResultFormat=JSON"
       "&datasetname=NIPA&TableName={table}&Frequency=Q&Year=LAST5")
# normalized title → (BEA NIPA table, unit)
TABLES: dict[str, tuple[str, str]] = {
    "gdp growth rate": ("T10101", "%"),
    "pce price index": ("T20804", "%"),
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
