# src/vike_trader_app/data/calendar/providers/ecb.py
"""ECB Statistical Data Warehouse actuals: EU rates/inflation. No API key required."""
from __future__ import annotations

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = "https://data-api.ecb.europa.eu/service/data/{flow}/{key}?lastNObservations=1&format=jsondata"
# Keyed by the NORMALIZED ForexFactory EUR title (e.g. "cpi estimate y/y", not "inflation
# rate") → (ECB SDW dataflow, series key, unit). Every key below was verified live against
# data-api.ecb.europa.eu (CPI y/y, core CPI y/y, euro-area unemployment, M3 growth).
SERIES: dict[str, tuple[str, str, str]] = {
    "cpi estimate y/y": ("ICP", "M.U2.N.000000.4.ANR", "%"),
    "core cpi estimate y/y": ("ICP", "M.U2.N.XEF000.4.ANR", "%"),
    "unemployment rate": ("LFSI", "M.I9.S.UNEHRT.TOTAL0.15_74.T", "%"),
    "m3 money supply y/y": ("BSI", "M.U2.Y.V.M30.X.I.U2.2300.Z01.A", "%"),
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
                # SDW returns full precision (e.g. M3 growth 2.7356); ForexFactory prints one decimal
                out[ev.id] = ActualValue(round(float(first[0]), 1), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
