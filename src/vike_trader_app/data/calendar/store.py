"""JSON-per-ISO-week cache for calendar events + a small fetch-time meta file.

Mirrors analysis/journal.py: all I/O through a base dir; corrupt files start clean.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .model import CalendarEvent

DEFAULT_ROOT = "storage/calendar"


class CalendarStore:
    def __init__(self, root: str = DEFAULT_ROOT):
        self.root = Path(root)
        self._meta_path = self.root / "meta.json"

    @staticmethod
    def iso_week_key(ts_utc: int) -> str:
        dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"

    def _week_path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def load_week(self, key: str) -> list[CalendarEvent]:
        p = self._week_path(key)
        if not p.exists():
            return []
        try:
            return [CalendarEvent.from_dict(d) for d in json.loads(p.read_text("utf-8"))]
        except (json.JSONDecodeError, TypeError, OSError):
            return []

    def save_week(self, key: str, events: list[CalendarEvent]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._week_path(key).write_text(
            json.dumps([e.to_dict() for e in events], indent=2), encoding="utf-8")

    def _meta(self) -> dict:
        if not self._meta_path.exists():
            return {}
        try:
            return json.loads(self._meta_path.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def last_fetch(self, key: str) -> int:
        return int(self._meta().get(key, 0))

    def mark_fetched(self, key: str, ts_ms: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        meta = self._meta()
        meta[key] = ts_ms
        self._meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
