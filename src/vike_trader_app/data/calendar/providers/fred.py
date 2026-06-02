# src/vike_trader_app/data/calendar/providers/fred.py
"""FRED (St. Louis Fed) actuals backfill for US events.

Maps a curated set of high/medium-impact US event titles to FRED series, fetches the
latest observation, and returns it as an ActualValue. Needs a free FRED_API_KEY; with
no key the provider is a no-op (graceful degradation).
"""
from __future__ import annotations

import os

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

OBS_URL = ("https://api.stlouisfed.org/fred/series/observations"
           "?series_id={series}&api_key={key}&file_type=json"
           "&sort_order=desc&limit=1")

# normalized US event title → (FRED series id, unit)
SERIES: dict[str, tuple[str, str]] = {
    "non-farm payrolls": ("PAYEMS", "K"),
    "unemployment rate": ("UNRATE", "%"),
    "inflation rate": ("CPIAUCSL", "%"),
    "core inflation rate": ("CPILFESL", "%"),
    "gdp growth rate": ("A191RL1Q225SBEA", "%"),
    "fed funds rate": ("FEDFUNDS", "%"),
    "retail sales": ("RSAFS", "%"),
}


class FredProvider:
    name = "FRED"

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("FRED_API_KEY")
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
            series, unit = mapped
            try:
                data = self._http(OBS_URL.format(series=series, key=self._key))
                obs = data.get("observations") or []
                if not obs or obs[0].get("value") in (None, ".", ""):
                    continue
                out[ev.id] = ActualValue(float(obs[0]["value"]), unit, self.name)
            except Exception:  # noqa: BLE001 - a flaky source must never break the calendar
                continue
        return out
