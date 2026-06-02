"""Economic-calendar event model + a small value holder.

A CalendarEvent is the normalized unit shared across providers, store, repository
and UI. Display strings (e.g. "−27.1 B A$") are authoritative for rendering; the
parsed (value, unit) drives beat/miss coloring and any future charting.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ActualValue:
    """One backfilled actual: parsed number + unit + which provider supplied it."""
    value: float | None
    unit: str
    source: str


@dataclass
class CalendarEvent:
    id: str
    ts_utc: int                 # epoch ms, UTC
    all_day: bool
    country: str                # normalized country name, e.g. "United States"
    currency: str               # ForexFactory code, e.g. "USD"
    title: str
    category: str               # rates|inflation|employment|gdp|trade|housing|other
    importance: int             # 0 low, 1 med, 2 high
    actual: float | None
    forecast: float | None
    previous: float | None
    unit: str
    actual_display: str
    forecast_display: str
    previous_display: str
    actual_source: str | None = None

    @staticmethod
    def make_id(ts_utc: int, currency: str, title: str) -> str:
        key = f"{ts_utc}|{currency}|{title}".encode("utf-8")
        return hashlib.sha1(key).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CalendarEvent":
        return cls(**d)
