# src/vike_trader_app/data/calendar/providers/bls.py
"""BLS (US Bureau of Labor Statistics) v2 API actuals: CPI, NFP, unemployment.

Optional BLS_API_KEY (higher limits); works keyless for light use. No-op on miss/error.
"""
from __future__ import annotations

import json
import os

from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title
from .base import ActualsProviderBase

URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# Keyed by normalized ForexFactory USD titles. Only "unemployment rate" is shipped: BLS
# returns the rate itself (%), a clean match (verified keyless). CPI/NFP from BLS are index
# LEVELS, not the m/m change ForexFactory reports, so they're left to FRED (which applies the
# right change/percent transform). BLS is a keyless fallback for when FRED has no key.
SERIES: dict[str, tuple[str, str]] = {
    "unemployment rate": ("LNS14000000", "%"),
}


class BlsProvider(ActualsProviderBase):
    name = "BLS"
    currencies = ("USD",)

    def __init__(self, api_key: str | None = None, http=None):
        self._key = api_key if api_key is not None else os.environ.get("BLS_API_KEY")
        # BLS uses POST; wrap a poster, but allow a fake http(url, data=...) in tests
        self._http = http

    def _post(self, series_id: str):
        if self._http is not None:
            return self._http(URL, data=series_id)
        import urllib.request
        body = json.dumps({"seriesid": [series_id],
                           **({"registrationkey": self._key} if self._key else {})}).encode()
        req = urllib.request.Request(URL, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))

    def _fetch_one(self, ev: CalendarEvent) -> ActualValue | None:
        mapped = SERIES.get(normalize_title(ev.title))
        if not mapped:
            return None
        series, unit = mapped
        rows = self._post(series)["Results"]["series"][0]["data"]
        if not rows:
            return None
        return ActualValue(float(rows[0]["value"]), unit, self.name)
