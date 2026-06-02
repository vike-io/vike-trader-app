"""Provider roles: a ScheduleProvider yields the event list; ActualsProviders fill
`actual` after release. Both are Protocols so any object with the right method fits.
"""
from __future__ import annotations

from typing import Protocol

from ..model import ActualValue, CalendarEvent


class ScheduleProvider(Protocol):
    def fetch_week(self, week_start_utc: int) -> list[CalendarEvent]: ...


class ActualsProvider(Protocol):
    name: str

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        """Return {event_id: ActualValue} for the events this provider can fill."""
        ...
