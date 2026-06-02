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
           "&units={units}&sort_order=desc&limit=1")

# Keyed by the NORMALIZED ForexFactory event title (the schedule provider), NOT the
# TradingView/TradingEconomics names — those differ ("Non-Farm Employment Change", not
# "Non-Farm Payrolls"). normalize_title() strips adv/prelim/final, so all GDP vintages
# collapse to "gdp q/q". Value → (FRED series, display unit, FRED `units` transform):
#   lin = level · chg = period change · pch = % change (MoM) · pc1 = % change vs year ago.
# The transform matters: ForexFactory reports the *change*/% (e.g. NFP ≈ +150K, CPI m/m
# 0.2%), while raw FRED series are mostly levels.
SERIES: dict[str, tuple[str, str, str]] = {
    "non-farm employment change": ("PAYEMS", "K", "chg"),
    "unemployment rate": ("UNRATE", "%", "lin"),
    "cpi m/m": ("CPIAUCSL", "%", "pch"),
    "cpi y/y": ("CPIAUCSL", "%", "pc1"),
    "core cpi m/m": ("CPILFESL", "%", "pch"),
    "core cpi y/y": ("CPILFESL", "%", "pc1"),
    "average hourly earnings m/m": ("CES0500000003", "%", "pch"),
    "retail sales m/m": ("RSAFS", "%", "pch"),
    "core retail sales m/m": ("RSFSXMV", "%", "pch"),
    "gdp q/q": ("A191RL1Q225SBEA", "%", "lin"),
    "federal funds rate": ("DFEDTARU", "%", "lin"),
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
            series, unit, units = mapped
            try:
                data = self._http(OBS_URL.format(series=series, key=self._key, units=units))
                obs = data.get("observations") or []
                if not obs or obs[0].get("value") in (None, ".", ""):
                    continue
                # FRED change/percent transforms carry many decimals; ForexFactory prints
                # one (0.2%, +139K), so round to keep the display faithful.
                out[ev.id] = ActualValue(round(float(obs[0]["value"]), 1), unit, self.name)
            except Exception:  # noqa: BLE001 - a flaky source must never break the calendar
                continue
        return out
