"""Economic-calendar event model + a small value holder.

A CalendarEvent is the normalized unit shared across providers, store, repository
and UI. Display strings (e.g. "−27.1 B A$") are authoritative for rendering; the
parsed (value, unit) drives beat/miss coloring and any future charting.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


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


_NUM_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def parse_value(raw: str) -> tuple[float | None, str]:
    """Split a ForexFactory display string into (number, unit).

    Handles unicode minus, magnitude letters (K/M/B), and currency/percent units.
    Returns (None, "") for blanks and em/en dashes.
    """
    if raw is None:
        return None, ""
    s = raw.strip().replace("−", "-")  # unicode minus → ascii
    if s in ("", "—", "–", "-"):
        return None, ""
    m = _NUM_RE.search(s)
    if not m:
        return None, ""
    value = float(m.group(0).replace(",", ""))
    unit = (s[: m.start()] + s[m.end():]).strip()
    return value, unit


def impact_to_importance(impact: str) -> int:
    return {"high": 2, "medium": 1, "low": 0}.get((impact or "").strip().lower(), 0)


def iso_to_ts_utc(iso: str) -> int:
    """ISO-8601 (with offset, or trailing 'Z') → epoch ms UTC."""
    s = iso.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def week_start_utc(ts_utc: int) -> int:
    """Monday 00:00:00 UTC of the ISO week containing ts_utc (epoch ms)."""
    dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
    monday = (dt - timedelta(days=dt.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)
