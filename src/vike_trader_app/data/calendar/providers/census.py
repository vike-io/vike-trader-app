# src/vike_trader_app/data/calendar/providers/census.py
"""US Census EITS actuals: retail sales m/m. Needs a (free, activated) CENSUS_API_KEY —
the EITS data API has no keyless tier. Verified against api.census.gov (MARTS MPCSM = the
seasonally-adjusted month-over-month percent change, matching ForexFactory's reported %)."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from ..http import http_get_json
from ..model import ActualValue, CalendarEvent
from ..taxonomy import normalize_title
from .base import ActualsProviderBase

# Fetch the program's series from last year onward, then filter the rows here. `time` is a
# predicate (not a get-variable); the response carries it back as a column.
URL = ("https://api.census.gov/data/timeseries/eits/{program}"
       "?get=cell_value,category_code,seasonally_adj,data_type_code"
       "&time=from+{since}&key={key}")  # `time` is a predicate; response returns it as a column
# normalized ForexFactory USD title → (program, category_code, data_type_code, unit)
# MPCSM = Monthly Percent Change, Seasonally adjusted. 44X72 = retail & food services,
# 44Y72 = retail ex-autos ("core").
SERIES: dict[str, tuple[str, str, str, str]] = {
    "retail sales m/m": ("marts", "44X72", "MPCSM", "%"),
    "core retail sales m/m": ("marts", "44Y72", "MPCSM", "%"),
}


class CensusProvider(ActualsProviderBase):
    name = "Census"
    currencies = ("USD",)

    def __init__(self, api_key: str | None = None, http=http_get_json):
        self._key = api_key if api_key is not None else os.environ.get("CENSUS_API_KEY")
        self._http = http

    def _fetch_one(self, ev: CalendarEvent) -> ActualValue | None:
        if not self._key:
            return None
        mapped = SERIES.get(normalize_title(ev.title))
        if not mapped:
            return None
        program, cat, dtype, unit = mapped
        year = datetime.fromtimestamp(ev.ts_utc / 1000, tz=timezone.utc).year
        rows = self._http(URL.format(program=program, since=year - 1, key=self._key))
        col = {name: i for i, name in enumerate(rows[0])}
        picks = [r for r in rows[1:]
                 if r[col["category_code"]] == cat
                 and r[col["data_type_code"]] == dtype
                 and r[col["seasonally_adj"]] == "yes"
                 and r[col["cell_value"]] not in (None, "", ".")]
        if not picks:
            return None
        latest = max(picks, key=lambda r: r[col["time"]])  # 'YYYY-MM' sorts chronologically
        return ActualValue(round(float(latest[col["cell_value"]]), 1), unit, self.name)
