# src/vike_trader_app/data/calendar/providers/ecb.py
"""ECB Statistical Data Warehouse actuals: EU rates/inflation. No API key required."""
from __future__ import annotations

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?lastNObservations=1&format=jsondata"
# normalized title → (dataflow, series key, unit)
SERIES: dict[str, tuple[str, str, str]] = {
    "inflation rate": ("ICP", "M.U2.N.000000.4.ANR", "%"),
    "core inflation rate": ("ICP", "M.U2.N.XEF000.4.ANR", "%"),
}


class EcbProvider:
    name = "ECB"

    def __init__(self, http=http_get_json):
        self._http = http

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "EUR" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            flow, key, unit = mapped
            try:
                data = self._http(URL.format(flow=flow, key=key))
                series = next(iter(data["dataSets"][0]["series"].values()))
                obs = series["observations"]
                first = next(iter(obs.values()))
                out[ev.id] = ActualValue(float(first[0]), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
