"""Provider roles: a ScheduleProvider yields the event list; ActualsProviders fill
`actual` after release. Both are Protocols so any object with the right method fits.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from ..model import ActualValue, CalendarEvent

_MAX_WORKERS = 6  # cap concurrent HTTP per provider; the 429 retry handles bursts


class ScheduleProvider(Protocol):
    def fetch_week(self, week_start_utc: int) -> list[CalendarEvent]: ...


class ActualsProvider(Protocol):
    name: str

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        """Return {event_id: ActualValue} for the events this provider can fill."""
        ...


class ActualsProviderBase:
    """Shared actuals-provider behavior: filter to applicable events, then fetch them
    CONCURRENTLY. The per-event HTTP call is what dominates first-load latency (FRED alone
    is ~10 serial calls otherwise), so a small thread pool collapses it to ~one call's time.
    Subclasses set `currencies` and implement `_fetch_one`.
    """

    name: str = "?"
    currencies: tuple[str, ...] = ()  # empty = all currencies

    def _fetch_one(self, ev: CalendarEvent) -> ActualValue | None:
        """Fetch+parse one event's actual, or None if unmapped/unavailable."""
        raise NotImplementedError

    def _safe_fetch(self, ev: CalendarEvent) -> ActualValue | None:
        try:
            return self._fetch_one(ev)
        except Exception:  # noqa: BLE001 - a flaky source must never break the calendar
            return None

    def backfill(self, events: list[CalendarEvent]) -> dict[str, ActualValue]:
        todo = [e for e in events
                if e.actual is None and (not self.currencies or e.currency in self.currencies)]
        if not todo:
            return {}
        out: dict[str, ActualValue] = {}
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(todo))) as ex:
            for ev, av in zip(todo, ex.map(self._safe_fetch, todo)):
                if av is not None:
                    out[ev.id] = av
        return out
