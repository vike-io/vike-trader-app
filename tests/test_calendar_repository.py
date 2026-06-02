# tests/test_calendar_repository.py
from vike_trader_app.data.calendar.repository import CalendarRepository
from vike_trader_app.data.calendar.store import CalendarStore
from vike_trader_app.data.calendar.model import (
    CalendarEvent, ActualValue, iso_to_ts_utc, week_start_utc,
)

TS = iso_to_ts_utc("2026-06-05T12:30:00+00:00")
WK = week_start_utc(TS)


def _sched_ev(title, actual=None):
    return CalendarEvent(
        id=CalendarEvent.make_id(TS, "USD", title), ts_utc=TS, all_day=False,
        country="United States", currency="USD", title=title, category="employment",
        importance=2, actual=actual, forecast=185.0, previous=177.0, unit="K",
        actual_display=("%sK" % actual if actual else ""),
        forecast_display="185K", previous_display="177K")


class _Sched:
    def __init__(self, evs): self._evs = evs
    def fetch_week(self, ws): return list(self._evs)


class _Actuals:
    name = "FAKE"
    def __init__(self, mapping): self._m = mapping
    def backfill(self, events):
        return {e.id: self._m[e.title] for e in events if e.title in self._m}


def test_fetches_and_caches(tmp_path):
    store = CalendarStore(str(tmp_path))
    repo = CalendarRepository(_Sched([_sched_ev("Non-Farm Payrolls")]), [], store,
                              now_ms=lambda: TS + 60_000)
    evs = repo.get_week(WK)
    assert [e.title for e in evs] == ["Non-Farm Payrolls"]
    # second call served from cache even if the schedule now errors
    repo2 = CalendarRepository(_Sched([]), [], store, now_ms=lambda: TS + 120_000)
    assert [e.title for e in repo2.get_week(WK)] == ["Non-Farm Payrolls"]


def test_backfills_actual_for_past_events(tmp_path):
    store = CalendarStore(str(tmp_path))
    actuals = _Actuals({"Non-Farm Payrolls": ActualValue(272.4, "K", "FAKE")})
    repo = CalendarRepository(_Sched([_sched_ev("Non-Farm Payrolls")]), [actuals], store,
                              now_ms=lambda: TS + 60_000)   # event is in the past
    ev = repo.get_week(WK)[0]
    assert ev.actual == 272.4 and ev.actual_source == "FAKE" and ev.actual_display == "272.4K"


def test_does_not_backfill_future_events(tmp_path):
    store = CalendarStore(str(tmp_path))
    actuals = _Actuals({"Non-Farm Payrolls": ActualValue(272.4, "K", "FAKE")})
    repo = CalendarRepository(_Sched([_sched_ev("Non-Farm Payrolls")]), [actuals], store,
                              now_ms=lambda: TS - 60_000)   # event still in the future
    assert repo.get_week(WK)[0].actual is None


def test_rate_limit_skips_refetch_when_fresh(tmp_path):
    store = CalendarStore(str(tmp_path))
    calls = {"n": 0}
    class Counting(_Sched):
        def fetch_week(self, ws):
            calls["n"] += 1
            return super().fetch_week(ws)
    sched = Counting([_sched_ev("CPI")])
    repo = CalendarRepository(sched, [], store, now_ms=lambda: TS,
                              min_refetch_ms=10 * 60_000)
    repo.get_week(WK)
    repo.get_week(WK)            # within the 10-min window → no second fetch
    assert calls["n"] == 1


def test_force_refetches_within_window(tmp_path):
    store = CalendarStore(str(tmp_path))
    calls = {"n": 0}
    class Counting(_Sched):
        def fetch_week(self, ws):
            calls["n"] += 1
            return super().fetch_week(ws)
    sched = Counting([_sched_ev("CPI")])
    repo = CalendarRepository(sched, [], store, now_ms=lambda: TS,
                              min_refetch_ms=10 * 60_000)
    repo.get_week(WK)
    repo.get_week(WK, force=True)     # force bypasses the fresh window
    assert calls["n"] == 2
