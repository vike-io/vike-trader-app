"""Pin-to-precompute rollups (Phase 3): materialize a higher timeframe from the 1m base.

For a *pinned* timeframe (one queried so often that on-the-fly resampling is wasteful — e.g. a
hot chart over a multi-million-bar NASDAQ base), ``refresh_rollup`` materialises it into its own
partitioned series so reads serve it directly (via ``Catalog`` / ``DuckCatalog.get_or_derive``)
instead of re-resampling. The refresh is:

- **incremental** — it recomputes only from the *watermark* (the last materialised bucket), not
  the whole base;
- **watermark-aware** — that bucket is *reopened* (recomputed from all its base bars) in case it
  was still partial when last materialised;
- **idempotent** — buckets are written by ``append_series`` (dedup by ts), so re-running with no
  new base data leaves the rollup unchanged.

Aggregation reuses ``core.timeframe.resample`` (the canonical, byte-identical rule), so a rollup
and an on-the-fly derive always agree. Pin only timeframes the source doesn't serve natively —
a pinned interval shares the ``<symbol>/<interval>/`` series with any native fetch of it.
"""

import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

from ..core.timeframe import parse_timeframe, resample
from . import state_db
from .parquet_source import append_series, read_series, read_series_since

log = logging.getLogger(__name__)


def rollup_refresh_start(watermark_ts: int | None, target_ms: int) -> int:
    """Epoch ms to recompute the rollup from: the start of the bucket holding ``watermark_ts``.

    None (no rollup yet) → 0, i.e. build from the beginning. Otherwise floor the watermark to its
    bucket boundary so that (possibly partial) last bucket is recomputed from all of its base bars.
    """
    if watermark_ts is None:
        return 0
    return watermark_ts - watermark_ts % target_ms


def refresh_rollup(root: str, symbol: str, interval: str, base: str = "1m") -> int:
    """Incrementally materialise ``interval`` for ``symbol`` from the ``base`` series.

    Returns the number of rollup bars (re)written this pass (0 if there's nothing to do). Rolling
    ``base`` into itself is a no-op.
    """
    if interval == base:
        return 0
    target_ms = parse_timeframe(interval)
    existing = read_series(root, symbol, interval)
    start = rollup_refresh_start(existing[-1].ts if existing else None, target_ms)
    base_bars = read_series_since(root, symbol, base, start)  # partition-pruned: reads only the tail
    if not base_bars:
        return 0
    rolled = resample(base_bars, target_ms)
    append_series(rolled, root, symbol, interval)  # dedup by ts -> reopens the last bucket, idempotent
    return len(rolled)


# --- pin registry: which (symbol, interval) series to keep precomputed ---------------------
#
# Pins live in the app DB (table ``rollup_pins``, one row per pinned pair — the natural key),
# per the state-in-DB rule (see ``state_db``). ``path`` everywhere below is the *legacy* JSON
# file (``storage/pins.json``) — read only by the one-time sweep, and kept as the first
# positional argument so existing callers (``ui.app``, ``ui.datamanager``) don't change. The DB
# lives beside it (``<dir>/db/vike_trader_app.sqlite``); ``db_path`` is the explicit test seam.

_PINS_TABLE = "rollup_pins"
_PINS_SCHEMA = """
CREATE TABLE IF NOT EXISTS rollup_pins (
    symbol   TEXT NOT NULL,
    interval TEXT NOT NULL,
    PRIMARY KEY (symbol, interval)
);
"""


def _open_pins(path: str, db_path=None) -> sqlite3.Connection:
    """Open the app DB with the pin table ensured, sweeping the legacy JSON file in once."""
    db = Path(db_path) if db_path is not None else state_db.db_for_file(path)
    conn = state_db.connect(db, _PINS_SCHEMA)
    state_db.sweep_once(conn, db, _PINS_TABLE, path,
                        lambda c: _sweep_legacy_pins(c, Path(path)))
    return conn


def _sweep_legacy_pins(conn: sqlite3.Connection, legacy: Path) -> None:
    """Import legacy pin pairs (DB wins via INSERT OR IGNORE), then delete the file.

    An unreadable file is left in place (and logged) — pins are user state, not a cache.
    """
    if not legacy.is_file():
        return
    try:
        pairs = [(str(s), str(i))
                 for s, i in json.loads(legacy.read_text(encoding="utf-8"))]
    except (json.JSONDecodeError, TypeError, ValueError, OSError):
        log.warning("rollup-pins migration: leaving unreadable %s in place", legacy)
        return
    if pairs:
        with conn:
            conn.executemany(
                "INSERT OR IGNORE INTO rollup_pins (symbol, interval) VALUES (?, ?)", pairs)
    try:
        legacy.unlink()
    except OSError as exc:
        log.warning("rollup-pins migration: could not delete %s (%s)", legacy, exc)
        return
    log.info("rollup-pins migration: moved %d pin(s) from %s into the app DB",
             len(pairs), legacy)


def load_pins(path: str, *, db_path=None) -> list[list[str]]:
    """Pinned ``[symbol, interval]`` pairs from the app DB (``[]`` when none are stored)."""
    try:
        with closing(_open_pins(path, db_path)) as conn:
            rows = conn.execute(
                "SELECT symbol, interval FROM rollup_pins ORDER BY symbol, interval"
            ).fetchall()
        return [[s, i] for s, i in rows]
    except sqlite3.Error:
        return []


def save_pins(path: str, pins: list, *, db_path=None) -> None:
    """Persist pinned ``(symbol, interval)`` pairs to the app DB (deduped, sorted)."""
    uniq = sorted({(s, i) for s, i in pins})
    with closing(_open_pins(path, db_path)) as conn, conn:  # one tx: rewrite the (tiny) set
        conn.execute("DELETE FROM rollup_pins")
        conn.executemany("INSERT INTO rollup_pins (symbol, interval) VALUES (?, ?)", uniq)


def refresh_pinned(root: str, pins: list) -> dict:
    """Refresh every pinned rollup; returns ``{"symbol/interval": bars_written}``."""
    return {f"{s}/{i}": refresh_rollup(root, s, i) for s, i in pins}
