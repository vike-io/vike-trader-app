# src/vike_trader_app/data/calendar/providers/bls.py
"""BLS (US Bureau of Labor Statistics) v2 API actuals: CPI, NFP, unemployment.

Optional BLS_API_KEY (higher limits); works keyless for light use. No-op on miss/error.
"""
from __future__ import annotations

import json
import os

from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title

URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
SERIES: dict[str, tuple[str, str]] = {
    "inflation rate": ("CUUR0000SA0", "%"),
    "core inflation rate": ("CUUR0000SA0L1E", "%"),
    "unemployment rate": ("LNS14000000", "%"),
    "non-farm payrolls": ("CES0000000001", "K"),
}


class BlsProvider:
    name = "BLS"

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

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        out: dict[str, ActualValue] = {}
        for ev in events:
            if ev.currency != "USD" or ev.actual is not None:
                continue
            mapped = SERIES.get(normalize_title(ev.title))
            if not mapped:
                continue
            series, unit = mapped
            try:
                data = self._post(series)
                rows = data["Results"]["series"][0]["data"]
                if rows:
                    out[ev.id] = ActualValue(float(rows[0]["value"]), unit, self.name)
            except Exception:  # noqa: BLE001
                continue
        return out
