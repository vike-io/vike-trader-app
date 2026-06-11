"""Shared seam for runtime-state tables in the app SQLite DB.

Why a database: per the project rule, **runtime state lives in the app's SQLite store**
(``storage/db/vike_trader_app.sqlite``), never in loose JSON files — files survive only as
Parquet bar data and explicit export/import artifacts. This module is the common idiom for the
simple stores migrated in state-in-DB #4 (alerts, journal, screener composites, rollup pins,
the provider configs, symbol mappings, DataSets), mirroring the merged predecessors
(:mod:`.instrument_db`, :mod:`vike_trader_app.ai.telemetry`, :mod:`.calendar.store`,
:mod:`.news.feeds_store`):

* connections are opened **per call** on the caller's thread with ``timeout=5`` (the app DB is
  shared across processes — GUI and MCP server — so a writer briefly holding the lock must make
  the others wait, not fail); no connection is held open or crosses a thread;
* ``CREATE TABLE IF NOT EXISTS`` runs on every open (each store's schema lives with the store);
* transactions stay tiny via ``contextlib.closing`` + the connection context manager;
* the legacy JSON store is swept into the DB **once per process** (memoized only after a
  successful sweep, so a transient failure is retried): rows go in with ``INSERT OR IGNORE`` so
  the DB wins on a re-sweep, then the file is deleted. Every store here is user-authored state,
  so a file that fails to parse is **left in place** (and logged) for hand recovery — never
  dropped like a refetchable cache.

Most of these stores keep the legacy file's whole-document semantics (one JSON list read and
written whole), so they persist it as a **single-row table**: ``id = 0`` plus the payload as one
JSON column — the ``calendar_weeks`` judgment from the calendar store, one codec with the legacy
file, no drift. Stores with a natural key (rollup pins, DataSets) normalize instead.

The DB file is derived from the legacy location: a legacy file sat directly in the storage root,
so its DB is ``<that dir>/db/vike_trader_app.sqlite`` — ``storage/alerts.json`` maps to the app
DB, and a test tmp root maps to an isolated tmp DB automatically. ``db_path`` parameters remain
the explicit test seam everywhere (never env vars).

Import-light on purpose: stdlib only — no :mod:`.store` (pandas-heavy) import.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

DB_DIRNAME = "db"
DB_FILENAME = "vike_trader_app.sqlite"

#: The production app DB (== ``data.store.DEFAULT_PATH``) — a literal so importing this module
#: never drags in the pandas-heavy run store (mirrors :mod:`vike_trader_app.ai.telemetry`).
DB_DEFAULT = "storage/db/vike_trader_app.sqlite"

#: (db, table, legacy-location) triples swept this process. The sweep itself is idempotent —
#: the memo just keeps hot paths from re-statting the legacy file on every call.
_MIGRATED: set[tuple[str, str, str]] = set()


def app_db_path(root: str | Path) -> Path:
    """The app DB for a config ``root``: ``<root>/db/vike_trader_app.sqlite``."""
    return Path(root) / DB_DIRNAME / DB_FILENAME


def db_for_file(legacy_path: str | Path) -> Path:
    """The app DB beside a legacy JSON file — the file's directory was the storage root."""
    return app_db_path(Path(legacy_path).parent)


def connect(db_path: str | Path, schema_sql: str) -> sqlite3.Connection:
    """Open the DB (creating dir + schema). Schema-only: never triggers a legacy sweep."""
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=5)
    conn.executescript(schema_sql)
    conn.commit()
    return conn


def sweep_once(conn: sqlite3.Connection, db_path: str | Path, table: str,
               legacy_path: str | Path, sweep: Callable[[sqlite3.Connection], None]) -> None:
    """Run ``sweep(conn)`` once per (db, table, legacy) per process; close ``conn`` on failure.

    The memo is added only after a successful sweep so a transient failure is retried on the
    next call. (Leaving an unreadable user file in place still counts as success — the sweep
    logged it and there is nothing more to retry.)
    """
    key = (os.fspath(Path(db_path).resolve()), table,
           os.fspath(Path(legacy_path).resolve()))
    if key in _MIGRATED:
        return
    try:
        sweep(conn)
        _MIGRATED.add(key)
    except Exception:
        conn.close()
        raise


# --- single-row blob stores -------------------------------------------------------------------

def _blob_schema(table: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {table} (\n"
        "    id      INTEGER PRIMARY KEY CHECK (id = 0),  -- single-row store\n"
        "    payload TEXT NOT NULL                        -- the whole legacy JSON document\n"
        ")"
    )


def open_blob(table: str, legacy_path: str | Path,
              db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the DB with ``table`` ensured, sweeping the legacy JSON file in once first."""
    db = Path(db_path) if db_path is not None else db_for_file(legacy_path)
    conn = connect(db, _blob_schema(table))
    sweep_once(conn, db, table, legacy_path,
               lambda c: _sweep_blob_file(c, table, Path(legacy_path)))
    return conn


def _sweep_blob_file(conn: sqlite3.Connection, table: str, legacy: Path) -> None:
    """Import the legacy JSON document as the single row (DB wins), then delete the file."""
    if not legacy.is_file():
        return
    try:
        raw = legacy.read_text(encoding="utf-8")
        json.loads(raw)  # validate only — stored verbatim; the store parses it on load
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        log.warning("%s migration: leaving unreadable %s in place", table, legacy)
        return
    with conn:
        conn.execute(f"INSERT OR IGNORE INTO {table} (id, payload) VALUES (0, ?)", (raw,))
    try:
        legacy.unlink()
    except OSError as exc:
        log.warning("%s migration: could not delete %s (%s)", table, legacy, exc)
        return
    log.info("%s migration: moved legacy %s into the app DB", table, legacy)


def load_blob(table: str, legacy_path: str | Path, *,
              db_path: str | Path | None = None):
    """The store's parsed JSON payload, or ``None`` when it has never been written.

    sqlite/IO errors also yield ``None`` — every store here treated an unreadable legacy file
    as absent, and ``None`` lets the caller apply its own default.
    """
    try:
        with closing(open_blob(table, legacy_path, db_path)) as conn:
            row = conn.execute(f"SELECT payload FROM {table} WHERE id = 0").fetchone()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, json.JSONDecodeError, OSError):
        return None


def save_blob(table: str, legacy_path: str | Path, payload, *,
              db_path: str | Path | None = None) -> None:
    """Persist ``payload`` (any JSON-encodable document) as the store's single row."""
    with closing(open_blob(table, legacy_path, db_path)) as conn, conn:
        conn.execute(f"INSERT OR REPLACE INTO {table} (id, payload) VALUES (0, ?)",
                     (json.dumps(payload),))
