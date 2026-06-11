"""SQLite-backed cache for calendar events: one row per ISO week + fetch-time meta.

Why a database: per the project rule, **runtime state lives in the app's SQLite store**
(``storage/db/vike_trader_app.sqlite``), never in loose JSON files. Each cached ISO week is one
``calendar_weeks`` row holding the event list as a single JSON payload — a week is only ever
loaded/saved *whole* (the repository rebuilds it per refresh), so a single-row read/write is
atomic by construction and the codec is the exact list-of-dicts the JSON files used (mirrors
:mod:`vike_trader_app.data.instrument_db`). Week fetch timestamps live in ``calendar_meta`` and
the Finnhub company-profile cache (see :mod:`.equity`) in ``calendar_profiles``. The legacy
``storage/calendar/`` JSON dir (``<YYYY-Wnn>.json`` + ``meta.json`` + ``profiles.json``) is
swept into the DB once by :func:`_migrate_legacy_dir`, then deleted.

Connections are opened per call with a busy timeout and transactions stay tiny — nothing holds
the DB open (the per-call idiom mirrors :mod:`vike_trader_app.ai.telemetry`): the app DB file is
shared with other writers (telemetry from the GUI *and* the MCP server process; the equity tab's
fetch worker thread), and no connection object ever crosses a thread.

Corrupt rows start clean, like the JSON store this replaces — it is a refetchable cache.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .model import CalendarEvent

log = logging.getLogger(__name__)

#: Where the legacy JSON store lived — read only by the one-time sweep.
DEFAULT_ROOT = "storage/calendar"

#: Default DB file == the app DB (``data.store.DEFAULT_PATH``). Kept as a literal so importing
#: this module never drags in the pandas-heavy run store (mirrors :mod:`vike_trader_app.ai.telemetry`).
DB_DEFAULT = "storage/db/vike_trader_app.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calendar_weeks (
    key     TEXT PRIMARY KEY,  -- ISO week key, e.g. '2026-W23'
    payload TEXT NOT NULL      -- the week's events as one JSON list (whole-week I/O)
);

CREATE TABLE IF NOT EXISTS calendar_meta (
    key   TEXT PRIMARY KEY,    -- ISO week key
    value TEXT NOT NULL        -- last schedule-fetch ts (ms epoch)
);

CREATE TABLE IF NOT EXISTS calendar_profiles (
    symbol  TEXT PRIMARY KEY,
    payload TEXT NOT NULL      -- JSON {'name', 'cap'} (see equity.profiles)
);
"""

_WEEK_FILE = re.compile(r"^\d{4}-W\d{2}$")  # legacy per-week filenames, e.g. 2026-W23.json

# (db, legacy root) pairs whose legacy JSON dir has been swept this process. The sweep itself is
# idempotent — the memo just keeps hot paths from re-statting the legacy dir on every call.
_MIGRATED: set[tuple[str, str]] = set()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open the DB (creating dir + schema). Schema-only: never triggers the legacy sweep.

    ``timeout=5`` is the cross-process/cross-thread busy timeout: the app DB is shared, so a
    writer briefly holding the lock must make the others wait, not fail.
    """
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def open_db(db_path: str | Path, legacy_root: str | Path) -> sqlite3.Connection:
    """The store entry point: open the DB, lazily sweeping the legacy JSON dir first.

    Every read/write goes through here, so the one-time migration runs before anything can
    observe (or shadow) the tables. The memo is added only after a successful sweep so a
    transient failure is retried on the next call.
    """
    key = (os.fspath(Path(db_path).resolve()), os.fspath(Path(legacy_root).resolve()))
    conn = connect(db_path)
    if key not in _MIGRATED:
        try:
            _migrate_legacy_dir(conn, Path(legacy_root))
            _MIGRATED.add(key)
        except Exception:
            conn.close()
            raise
    return conn


def _migrate_legacy_dir(conn: sqlite3.Connection, root: Path) -> None:
    """Sweep the legacy ``storage/calendar/`` JSON dir into the DB, then delete it.

    Idempotent, DB-wins: rows go in with ``INSERT OR IGNORE`` so a re-run after a partial
    failure can never clobber state written through the DB since. Every *recognized* file is
    deleted — imported, superseded, or unreadable alike: this is a refetchable cache, and the
    legacy reader already treated corrupt files as empty. Unrecognized files are left in place
    (and keep the dir alive); the emptied dir is removed.
    """
    if not root.is_dir():
        return
    handled = 0
    for f in sorted(root.glob("*.json")):
        stem = f.stem
        try:
            raw = json.loads(f.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("calendar migration: dropping unreadable legacy cache %s", f)
            raw = None
        if raw is not None:
            if stem == "meta" and isinstance(raw, dict):
                with conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO calendar_meta (key, value) VALUES (?, ?)",
                        [(str(k), str(v)) for k, v in raw.items()])
            elif stem == "profiles" and isinstance(raw, dict):
                with conn:
                    conn.executemany(
                        "INSERT OR IGNORE INTO calendar_profiles (symbol, payload) VALUES (?, ?)",
                        [(str(s), json.dumps(v)) for s, v in raw.items()])
            elif _WEEK_FILE.match(stem) and isinstance(raw, list):
                with conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO calendar_weeks (key, payload) VALUES (?, ?)",
                        (stem, json.dumps(raw)))
            else:
                log.warning("calendar migration: leaving unrecognized %s in place", f)
                continue
        try:
            f.unlink()
            handled += 1
        except OSError as exc:
            log.warning("calendar migration: could not delete %s (%s)", f, exc)
    try:  # leave no empty legacy dir behind (best-effort; unknown extra files keep it alive)
        root.rmdir()
    except OSError:
        pass
    if handled:
        log.info("calendar migration: moved %d legacy JSON file(s) from %s into the app DB",
                 handled, root)


class CalendarStore:
    """Week-keyed event cache in the app DB. Method signatures match the old JSON-file store.

    ``root`` is the *legacy* JSON dir — read only by the one-time sweep (kept as the first
    positional argument so existing callers don't change). ``db_path`` is the test seam:
    point it at a tmp file; it defaults to the shared app DB.
    """

    def __init__(self, root: str = DEFAULT_ROOT, *, db_path: str | Path | None = None):
        self.root = Path(root)
        self.db = Path(db_path) if db_path is not None else Path(DB_DEFAULT)

    @staticmethod
    def iso_week_key(ts_utc: int) -> str:
        dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"

    def _open(self) -> sqlite3.Connection:
        return open_db(self.db, self.root)

    def load_week(self, key: str) -> list[CalendarEvent]:
        try:
            with closing(self._open()) as conn:
                row = conn.execute(
                    "SELECT payload FROM calendar_weeks WHERE key = ?", (key,)).fetchone()
            if row is None:
                return []
            return [CalendarEvent.from_dict(d) for d in json.loads(row[0])]
        except (sqlite3.Error, json.JSONDecodeError, TypeError, OSError):
            return []

    def save_week(self, key: str, events: list[CalendarEvent]) -> None:
        payload = json.dumps([e.to_dict() for e in events])
        with closing(self._open()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO calendar_weeks (key, payload) VALUES (?, ?)",
                (key, payload))

    def last_fetch(self, key: str) -> int:
        try:
            with closing(self._open()) as conn:
                row = conn.execute(
                    "SELECT value FROM calendar_meta WHERE key = ?", (key,)).fetchone()
            return int(row[0]) if row else 0
        except (sqlite3.Error, ValueError, OSError):
            return 0

    def mark_fetched(self, key: str, ts_ms: int) -> None:
        with closing(self._open()) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO calendar_meta (key, value) VALUES (?, ?)",
                (key, str(int(ts_ms))))
