"""state_db: the migrate-once + single-row blob seam under ~10 runtime stores.

Pins the failure contracts the happy-path store tests don't exercise: a raising sweep must NOT be
memoized (so it retries) and must close the connection; an unreadable legacy file must be left in
place and read as absent.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from vike_trader_app.data import state_db


def test_sweep_once_failure_reraises_closes_conn_and_does_not_memoize(tmp_path):
    db = tmp_path / "x.db"
    table, legacy = "t", tmp_path / "legacy.json"
    conn = sqlite3.connect(db)
    calls = []

    def boom(_c):
        calls.append("boom")
        raise RuntimeError("transient")

    with pytest.raises(RuntimeError):
        state_db.sweep_once(conn, db, table, legacy, boom)

    # connection closed on failure
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")

    # NOT memoized -> a transient failure is retried on the next call
    key = (os.fspath(Path(db).resolve()), table, os.fspath(Path(legacy).resolve()))
    assert key not in state_db._MIGRATED
    conn2 = sqlite3.connect(db)
    state_db.sweep_once(conn2, db, table, legacy, lambda _c: calls.append("ok"))
    conn2.close()
    assert calls == ["boom", "ok"]            # ran again, then succeeded
    assert key in state_db._MIGRATED          # success memoized -> no third run


def test_sweep_once_success_is_memoized(tmp_path):
    db, legacy = tmp_path / "m.db", tmp_path / "m.json"
    conn = sqlite3.connect(db)
    runs = []
    state_db.sweep_once(conn, db, "tbl", legacy, lambda _c: runs.append(1))
    state_db.sweep_once(conn, db, "tbl", legacy, lambda _c: runs.append(1))  # no-op
    conn.close()
    assert runs == [1]


def test_undecodable_legacy_is_left_in_place_and_load_returns_none(tmp_path):
    legacy = tmp_path / "store.json"
    legacy.write_bytes(b"\xff\xfe not utf-8, not json")
    db = tmp_path / "store.db"
    assert state_db.load_blob("mystore", legacy, db_path=db) is None
    assert legacy.exists()                     # an unreadable user file is NEVER deleted


def test_legacy_json_swept_into_db_then_deleted(tmp_path):
    legacy, db = tmp_path / "leg.json", tmp_path / "leg.db"
    legacy.write_text('{"x": 42}', encoding="utf-8")
    assert state_db.load_blob("lt", legacy, db_path=db) == {"x": 42}
    assert not legacy.exists()                 # swept into the DB -> source file removed


def test_save_then_load_roundtrip(tmp_path):
    legacy, db = tmp_path / "s.json", tmp_path / "s.db"
    state_db.save_blob("st", legacy, {"a": 1, "b": [1, 2, 3]}, db_path=db)
    assert state_db.load_blob("st", legacy, db_path=db) == {"a": 1, "b": [1, 2, 3]}
