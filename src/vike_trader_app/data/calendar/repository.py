"""Aggregator: schedule + actuals + cache.

get_week() loads the cached ISO week, refetches the schedule when stale (respecting a
min-refetch window), merges by id, backfills `actual` for past events via the actuals
providers in priority order, persists, and returns events sorted by time.
"""
from __future__ import annotations

import time

from .model import CalendarEvent
from .store import CalendarStore

_MIN_REFETCH_MS = 10 * 60_000  # ForexFactory: ~2 downloads / 5 min — stay well under


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


class CalendarRepository:
    def __init__(self, schedule, actuals_providers, store: CalendarStore, *,
                 now_ms=lambda: int(time.time() * 1000), min_refetch_ms: int = _MIN_REFETCH_MS):
        self._schedule = schedule
        self._actuals = list(actuals_providers)
        self._store = store
        self._now = now_ms
        self._min_refetch = min_refetch_ms

    def get_week(self, week_start_utc: int, *, force: bool = False) -> list[CalendarEvent]:
        key = self._store.iso_week_key(week_start_utc)
        cached = {e.id: e for e in self._store.load_week(key)}

        if force or self._is_stale(key):
            try:
                fetched = self._schedule.fetch_week(week_start_utc)
                cached = self._merge(cached, fetched)
                self._store.mark_fetched(key, self._now())
            except Exception:  # noqa: BLE001 - keep serving cache if the source is down
                pass

        self._backfill(cached)
        events = sorted(cached.values(), key=lambda e: (e.ts_utc, e.country, e.title))
        self._store.save_week(key, events)
        return events

    def _is_stale(self, key: str) -> bool:
        return (self._now() - self._store.last_fetch(key)) >= self._min_refetch

    @staticmethod
    def _merge(cached: dict, fetched: list) -> dict:
        for ev in fetched:
            old = cached.get(ev.id)
            if old is not None and old.actual is not None:
                # preserve an already-backfilled actual; refresh schedule fields
                ev.actual, ev.actual_display, ev.actual_source = (
                    old.actual, old.actual_display, old.actual_source)
            cached[ev.id] = ev
        return cached

    def _backfill(self, cached: dict) -> None:
        now = self._now()
        pending = [e for e in cached.values() if e.actual is None and e.ts_utc <= now]
        if not pending:
            return
        for provider in self._actuals:
            if not pending:
                break
            try:
                filled = provider.backfill(pending)
            except Exception:  # noqa: BLE001
                filled = {}
            for ev_id, av in filled.items():
                ev = cached.get(ev_id)
                if ev is None or ev.actual is not None or av.value is None:
                    continue
                ev.actual = av.value
                ev.unit = ev.unit or av.unit
                ev.actual_display = f"{_fmt(av.value)}{ev.unit}"
                ev.actual_source = av.source
            pending = [e for e in pending if e.actual is None]


def default_repository(root: str = "storage/calendar",
                       config_root: str | None = None) -> "CalendarRepository":
    """Wire the real providers from env keys. Missing keys disable a provider silently.

    ``config_root``: when given AND an event_providers.json file exists there, actuals providers
    are filtered to the enabled set (and re-ordered accordingly). Callers that pass nothing get
    identical behavior to before — fully non-breaking.
    """
    from .providers.forexfactory import ForexFactoryProvider
    from .providers.fred import FredProvider
    from .providers.bls import BlsProvider
    from .providers.bea import BeaProvider
    from .providers.census import CensusProvider
    from .providers.ecb import EcbProvider
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass
    actuals_default = [FredProvider(), BlsProvider(), BeaProvider(), CensusProvider(), EcbProvider()]

    if config_root is not None:
        from ..event_providers_config import enabled_event_providers
        enabled = enabled_event_providers(config_root)
        if enabled is not None:
            # Filter and re-order actuals providers to match the config
            actuals_by_name = {p.name: p for p in actuals_default}
            # Preserve the config ordering; skip providers not in the enabled set
            actuals = [actuals_by_name[name] for name in
                       # iterate in config order; calendar actuals names only
                       [p.name for p in actuals_default if p.name in enabled]]
        else:
            actuals = actuals_default
    else:
        actuals = actuals_default

    return CalendarRepository(ForexFactoryProvider(), actuals, CalendarStore(root))
