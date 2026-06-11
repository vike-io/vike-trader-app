"""Opt-in usage telemetry for the MCP tools.

Records each tool call (name, timing, ok/error, a SAFE arg summary) so the app
publisher can analyze how the AI tools are used — WITHOUT routing the MCP traffic
through the cloud and WITHOUT changing who pays for inference (the user's own
Claude subscription still bills the model). The local MCP server already sees every
tool call; this just logs it and, optionally, reports it.

Privacy-first:
  * OFF by default — nothing is recorded unless explicitly enabled.
  * Strategy SOURCE is never sent: only a sha256 prefix + length.
  * The client id is a random per-install UUID (no machine name, no PII).
  * Remote reporting is best-effort on a background thread and can never break,
    slow, or fail a tool call.

Why a database: per the project rule, **runtime state lives in the app's SQLite store**
(``storage/db/vike_trader_app.sqlite``), never in loose files. The anonymous client id sits in
``telemetry_meta`` and each usage event becomes a ``telemetry_usage`` row; the legacy
``storage/telemetry/`` file store (``client_id`` + ``mcp-usage.jsonl``) is swept into the DB
once by :func:`_migrate_legacy_files`, then deleted. Telemetry is written from BOTH the GUI
process and the local MCP server process, so connections are opened per call with a busy
timeout and transactions stay tiny — nothing ever holds the DB open (contrast
:class:`vike_trader_app.data.store.Store`, whose long-lived single-process connection is the
wrong shape here; the per-call idiom mirrors :mod:`vike_trader_app.data.instrument_db`).

Enable via environment (the app publisher sets these when shipping):
    VIKE_TELEMETRY=1                  # turn on (records usage rows in the app DB)
    VIKE_TELEMETRY_URL=https://...    # also POST each event to this endpoint
    VIKE_TELEMETRY_DB=storage/db/vike_trader_app.sqlite
                                      # SQLite file holding telemetry_meta / telemetry_usage
    VIKE_TELEMETRY_DIR=storage/telemetry   # LEGACY file store, only read by the one-time sweep
    VIKE_TELEMETRY_TOKEN=...          # shared secret sent as the ``x-vike-token`` header
                                      # (must match the receiver's VIKE_TELEMETRY_TOKEN)
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)

_SCALAR = (str, int, float, bool, type(None))
# Argument names whose VALUE is sensitive (user source) — replaced with a sha+len.
_SOURCE_ARGS = ("strategy_code",)

# Default DB file == the app DB (data.store.DEFAULT_PATH). Kept as a literal because this module
# deliberately imports nothing from the package: the MCP server's telemetry must never be broken
# by a data-layer import.
_DB_DEFAULT = "storage/db/vike_trader_app.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS telemetry_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS telemetry_usage (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,  -- epoch seconds (event ts_ms / 1000)
    event TEXT NOT NULL   -- the full JSON event payload
);
"""

# (db, legacy dir) pairs whose legacy file store has been swept this process. The sweep itself is
# idempotent — the memo just keeps the per-event hot path from re-statting the legacy directory.
_MIGRATED: set[tuple[str, str]] = set()


def db_path() -> Path:
    """The SQLite file holding the telemetry tables (``VIKE_TELEMETRY_DB``, else the app DB)."""
    return Path(os.environ.get("VIKE_TELEMETRY_DB") or _DB_DEFAULT)


def _legacy_dir() -> Path:
    """Where the pre-DB file store lived (``client_id`` + ``mcp-usage.jsonl``); migration only."""
    return Path(os.environ.get("VIKE_TELEMETRY_DIR", "storage/telemetry"))


def _connect() -> sqlite3.Connection:
    """Open the DB (creating dir + schema). Schema-only: never triggers the legacy sweep.

    ``timeout=5`` is the cross-process busy timeout: the GUI app and the MCP server process
    write the same file, so a writer briefly holding the lock must make the other wait, not fail.
    """
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _open_db() -> sqlite3.Connection:
    """The store entry point: open the DB, lazily sweeping the legacy file store first.

    Every read/write goes through here, so the one-time migration runs before anything can
    observe (or shadow) the tables. The memo is added only after a successful sweep so a
    transient failure is retried on the next call.
    """
    key = (os.fspath(db_path()), os.fspath(_legacy_dir()))
    conn = _connect()
    if key not in _MIGRATED:
        try:
            _migrate_legacy_files(conn)
            _MIGRATED.add(key)
        except Exception:
            conn.close()
            raise
    return conn


def _migrate_legacy_files(conn: sqlite3.Connection) -> None:
    """Sweep the legacy ``storage/telemetry/`` file store into the DB, then delete it.

    Idempotent: the stored ``client_id`` wins over the file (``INSERT OR IGNORE``) so a re-run
    can never clobber the id other rows were recorded under; every handled file is deleted and
    the empty dir removed — after this nothing reads or writes those files. An unparseable
    JSONL line is skipped (logged): usage telemetry is low-value by design, losing a corrupt
    line beats keeping a dead file store alive.
    """
    d = _legacy_dir()
    if not d.is_dir():
        return
    cid_file = d / "client_id"
    if cid_file.is_file():
        cid = cid_file.read_text(encoding="utf-8").strip()
        if cid:
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO telemetry_meta (key, value) VALUES ('client_id', ?)",
                    (cid,),
                )
        cid_file.unlink()
        log.info("telemetry migration: moved legacy client_id into the app DB")
    jsonl = d / "mcp-usage.jsonl"
    if jsonl.is_file():
        rows: list[tuple[float, str]] = []
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                log.warning("telemetry migration: skipping unparseable line in %s", jsonl)
                continue
            ts_ms = ev.get("ts_ms") if isinstance(ev, dict) else None
            ts = ts_ms / 1000.0 if isinstance(ts_ms, (int, float)) else 0.0
            rows.append((ts, json.dumps(ev)))
        if rows:
            with conn:
                conn.executemany("INSERT INTO telemetry_usage (ts, event) VALUES (?, ?)", rows)
        jsonl.unlink()
        log.info("telemetry migration: imported %d event(s) from legacy mcp-usage.jsonl", len(rows))
    try:  # leave no empty legacy dir behind (best-effort; unknown extra files keep it alive)
        d.rmdir()
    except OSError:
        pass


def enabled() -> bool:
    """True when telemetry is switched on (default OFF)."""
    return os.environ.get("VIKE_TELEMETRY", "").strip().lower() in ("1", "true", "on", "yes")


def _endpoint() -> str | None:
    url = os.environ.get("VIKE_TELEMETRY_URL", "").strip()
    return url or None


def _token() -> str | None:
    """Shared secret for the receiver; sent as the ``x-vike-token`` header when set."""
    tok = os.environ.get("VIKE_TELEMETRY_TOKEN", "").strip()
    return tok or None


def _client_id() -> str:
    """Stable anonymous id for this install (random UUID in ``telemetry_meta``; no PII).

    Lazy-created on first use. ``INSERT OR IGNORE`` + re-read makes the cross-process race
    safe: if the GUI and the MCP server both create one, the first insert wins and both
    report under the same id.
    """
    try:
        with closing(_open_db()) as conn:
            row = conn.execute("SELECT value FROM telemetry_meta WHERE key = 'client_id'").fetchone()
            if row:
                return row[0]
            cid = uuid.uuid4().hex
            with conn:
                conn.execute(
                    "INSERT OR IGNORE INTO telemetry_meta (key, value) VALUES ('client_id', ?)",
                    (cid,),
                )
            row = conn.execute("SELECT value FROM telemetry_meta WHERE key = 'client_id'").fetchone()
            return row[0] if row else cid
    except (sqlite3.Error, OSError):
        return "anonymous"


def _safe_args(kwargs: dict) -> dict:
    """Whitelist scalars; replace strategy source with sha+len; summarize collections."""
    out: dict = {}
    for k, v in kwargs.items():
        if k in _SOURCE_ARGS and isinstance(v, str):
            out["strategy_code_sha"] = hashlib.sha256(v.encode("utf-8")).hexdigest()[:12]
            out["strategy_code_len"] = len(v)
        elif isinstance(v, _SCALAR):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = {"_len": len(v)}
        elif isinstance(v, dict):
            out[k] = {"_keys": sorted(map(str, v.keys()))[:20]}
        else:
            out[k] = f"<{type(v).__name__}>"
    return out


def _post(event: dict, url: str) -> None:
    """Best-effort POST of one event; all errors swallowed (telemetry is never load-bearing)."""
    try:
        import urllib.request

        data = json.dumps(event).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = _token()
        if token:
            headers["x-vike-token"] = token
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass


def record(event: dict) -> None:
    """Insert ``event`` into ``telemetry_usage`` and (if configured) POST it — best-effort.

    One tiny INSERT transaction on a per-call connection (see module docstring): the local
    record can never wedge the app DB for the other process. The remote POST is unchanged —
    per-event, background thread, fire-and-forget.
    """
    try:
        ts_ms = event.get("ts_ms")
        ts = ts_ms / 1000.0 if isinstance(ts_ms, (int, float)) else time.time()
        with closing(_open_db()) as conn, conn:
            conn.execute(
                "INSERT INTO telemetry_usage (ts, event) VALUES (?, ?)", (ts, json.dumps(event))
            )
    except (sqlite3.Error, OSError):
        pass
    url = _endpoint()
    if url:
        threading.Thread(target=_post, args=(event, url), daemon=True).start()


def _crash_enabled() -> bool:
    """Crash reporting opt-in. ``VIKE_CRASH_REPORTS`` overrides; otherwise follows telemetry."""
    raw = os.environ.get("VIKE_CRASH_REPORTS", "").strip().lower()
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    return enabled()


def _safe_env(app_version: str | None = None) -> dict:
    """Non-PII environment block for triage (versions/platform only)."""
    import platform as _platform
    import sys as _sys

    qt = None
    try:  # best-effort; PySide6 may be absent in headless installs
        import PySide6

        qt = getattr(PySide6, "__version__", None)
    except Exception:  # noqa: BLE001
        qt = None
    return {
        "app_version": app_version,
        "python": _sys.version.split()[0],
        "qt": qt,
        "platform": _sys.platform,
        "os": _platform.platform(),
    }


def report_crash(event: dict) -> None:
    """Upload one crash event via the telemetry channel — opt-in, best-effort, never raises.

    ``event`` carries the scrubbed crash payload built by :mod:`vike_trader_app.crash`
    (``kind``/``exc_type``/``traceback``/``app_version``/``ts_ms``). We stamp the crash
    ``type``, anonymous client id, and a safe env block, then reuse :func:`record` (a
    ``telemetry_usage`` row + background POST). Gated by :func:`_crash_enabled`.
    """
    try:
        if not _crash_enabled():
            return
        payload = dict(event)
        payload["type"] = "crash"
        payload.setdefault("ts_ms", int(time.time() * 1000))
        payload["client"] = _client_id()
        payload["env"] = _safe_env(event.get("app_version"))
        record(payload)
        # Also forward to Bugsnag (dedicated vike-trader project) for grouping/dedup/alerting —
        # best-effort, only when BUGSNAG_API_KEY is set; never breaks the local record above.
        try:
            from . import bugsnag

            bugsnag.report_crash(payload)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - crash reporting must never raise
        pass


def instrument(fn):
    """Wrap an MCP tool ``fn`` to record one telemetry event per call (no-op when disabled).

    ``functools.wraps`` preserves the wrapped function's name/signature/annotations, so FastMCP
    still derives the correct JSON schema from it. When telemetry is off the wrapper adds nothing
    but a single boolean check.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not enabled():
            return fn(*args, **kwargs)
        t0 = time.monotonic()
        ok, err = True, None
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            ok, err = False, type(e).__name__
            raise
        finally:
            record({
                "ts_ms": int(time.time() * 1000),
                "client": _client_id(),
                "tool": fn.__name__,
                "args": _safe_args(kwargs),
                "ok": ok,
                "error": err,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            })

    return wrapper
