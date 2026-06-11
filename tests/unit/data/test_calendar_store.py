# tests/test_calendar_store.py
"""CalendarStore — week cache in the app SQLite DB (state-in-DB rule), not JSON files.

Every test points BOTH seams (db_path + the legacy root) inside tmp_path so a developer's
real app DB / legacy ``storage/calendar`` dir is never read, written, or swept by the suite.
"""
import json
import sqlite3
from contextlib import closing

from vike_trader_app.data.calendar import store as store_mod
from vike_trader_app.data.calendar.store import CalendarStore
from vike_trader_app.data.calendar.model import CalendarEvent, iso_to_ts_utc


def _ev(ts, title):
    return CalendarEvent(
        id=CalendarEvent.make_id(ts, "USD", title), ts_utc=ts, all_day=False,
        country="United States", currency="USD", title=title, category="other",
        importance=1, actual=None, forecast=None, previous=None, unit="",
        actual_display="", forecast_display="", previous_display="")


def _store(tmp_path):
    """A store pointed entirely inside tmp_path: legacy dir + DB (never the real app DB)."""
    return CalendarStore(str(tmp_path / "calendar"), db_path=str(tmp_path / "app.sqlite"))


def test_iso_week_key_format():
    ts = iso_to_ts_utc("2026-06-02T00:00:00+00:00")
    assert CalendarStore.iso_week_key(ts) == "2026-W23"


def test_save_and_load_roundtrip(tmp_path):
    store = _store(tmp_path)
    ts = iso_to_ts_utc("2026-06-02T12:00:00+00:00")
    store.save_week("2026-W23", [_ev(ts, "CPI")])
    again = _store(tmp_path).load_week("2026-W23")   # fresh instance: read from the DB
    assert [e.title for e in again] == ["CPI"]


def test_load_missing_week_returns_empty(tmp_path):
    assert _store(tmp_path).load_week("1999-W01") == []


def test_meta_tracks_last_fetch(tmp_path):
    store = _store(tmp_path)
    assert store.last_fetch("2026-W23") == 0
    store.mark_fetched("2026-W23", 1_700_000_000_000)
    assert _store(tmp_path).last_fetch("2026-W23") == 1_700_000_000_000


# --- one-time legacy JSON migration ---------------------------------------------------

def test_migrates_legacy_dir_into_db_and_deletes_it(tmp_path):
    legacy = tmp_path / "calendar"
    legacy.mkdir()
    ts = iso_to_ts_utc("2026-06-02T12:00:00+00:00")
    (legacy / "2026-W23.json").write_text(
        json.dumps([_ev(ts, "CPI").to_dict()]), encoding="utf-8")
    (legacy / "meta.json").write_text(json.dumps({"2026-W23": 123}), encoding="utf-8")
    (legacy / "profiles.json").write_text(
        json.dumps({"AAPL": {"name": "Apple Inc", "cap": 3.5e6}}), encoding="utf-8")

    store = _store(tmp_path)
    assert [e.title for e in store.load_week("2026-W23")] == ["CPI"]  # first touch sweeps
    assert store.last_fetch("2026-W23") == 123
    with closing(sqlite3.connect(tmp_path / "app.sqlite")) as conn:
        row = conn.execute(
            "SELECT payload FROM calendar_profiles WHERE symbol = 'AAPL'").fetchone()
    assert json.loads(row[0]) == {"name": "Apple Inc", "cap": 3.5e6}
    assert not legacy.exists()  # files imported + deleted, emptied dir removed


def test_migration_db_rows_win_over_legacy_files(tmp_path, monkeypatch):
    store = _store(tmp_path)
    ts = iso_to_ts_utc("2026-06-02T12:00:00+00:00")
    store.save_week("2026-W23", [_ev(ts, "From DB")])

    legacy = tmp_path / "calendar"
    legacy.mkdir()
    (legacy / "2026-W23.json").write_text(
        json.dumps([_ev(ts, "From stale file").to_dict()]), encoding="utf-8")
    monkeypatch.setattr(store_mod, "_MIGRATED", set())  # force the sweep to re-run

    assert [e.title for e in _store(tmp_path).load_week("2026-W23")] == ["From DB"]
    assert not legacy.exists()  # superseded file still deleted — the DB is truth now


def test_migration_drops_unreadable_cache_files(tmp_path):
    legacy = tmp_path / "calendar"
    legacy.mkdir()
    (legacy / "2026-W23.json").write_text("{ not json", encoding="utf-8")

    assert _store(tmp_path).load_week("2026-W23") == []
    assert not legacy.exists()  # a corrupt week is a dead cache: dropped, dir removed


def test_migration_leaves_unrecognized_files_in_place(tmp_path):
    legacy = tmp_path / "calendar"
    legacy.mkdir()
    (legacy / "meta.json").write_text(json.dumps({"2026-W23": 9}), encoding="utf-8")
    (legacy / "custom.json").write_text(json.dumps({"mine": 1}), encoding="utf-8")

    assert _store(tmp_path).last_fetch("2026-W23") == 9   # meta swept
    assert not (legacy / "meta.json").exists()
    assert (legacy / "custom.json").exists()              # unknown file untouched
    assert legacy.exists()                                # non-empty dir kept
