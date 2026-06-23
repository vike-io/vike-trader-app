"""Durable ledger for live execution: append-only event audit + order/fill/position snapshots.

Reuses the ``data.state_db`` per-call-connection idiom (timeout=5, ``CREATE TABLE IF NOT EXISTS`` on
every open, the caller's thread) — NOT the long-lived single-connection ``data.store.Store`` (whose
held connection would force off-thread writes that violate the data-layer-not-thread-safe rule). The
append-only ``exec_events`` table is the audit trail + free Journal feed; ``exec_orders``/
``exec_fills``/``exec_positions`` are snapshot rows for fast cold-start. Fills dedup on ``trade_id``
so a reconnect replay never double-counts.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from vike_trader_app.data import state_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exec_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    client_order_id TEXT,
    payload         TEXT NOT NULL          -- JSON of the event
);
CREATE TABLE IF NOT EXISTS exec_orders (
    client_order_id TEXT PRIMARY KEY,
    venue           TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            INTEGER NOT NULL,
    qty             REAL NOT NULL,
    order_type      TEXT NOT NULL,
    price           REAL,
    trigger_price   REAL,
    status          TEXT NOT NULL,
    venue_order_id  TEXT,
    filled_qty      REAL NOT NULL DEFAULT 0,
    avg_fill_px     REAL NOT NULL DEFAULT 0,
    updated_ts      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exec_fills (
    trade_id        TEXT PRIMARY KEY,       -- dedup key across reconnects
    client_order_id TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            INTEGER NOT NULL,
    qty             REAL NOT NULL,
    px              REAL NOT NULL,
    commission      REAL NOT NULL DEFAULT 0,
    ts              INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exec_positions (
    venue         TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    position_side TEXT NOT NULL,            -- 'BOTH' | 'LONG' | 'SHORT' (hedge perps)
    qty           REAL NOT NULL,
    avg_px        REAL NOT NULL,
    realized_pnl  REAL NOT NULL DEFAULT 0,
    updated_ts    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (venue, symbol, position_side)
);
"""


def connect_exec_db(db_path: str | Path) -> sqlite3.Connection:
    """Open (creating dir + schema) the exec ledger DB. Per-call connection, caller's thread."""
    return state_db.connect(db_path, _SCHEMA)


def append_event(conn: sqlite3.Connection, *, ts: int, kind: str,
                 client_order_id: str | None, payload: str) -> None:
    """Append one immutable event to the audit trail."""
    with conn:
        conn.execute(
            "INSERT INTO exec_events (ts, kind, client_order_id, payload) VALUES (?, ?, ?, ?)",
            (ts, kind, client_order_id, payload))


def record_fill(conn: sqlite3.Connection, *, trade_id: str, client_order_id: str, symbol: str,
                side: int, qty: float, px: float, commission: float = 0.0, ts: int = 0) -> bool:
    """Record a fill, idempotent on ``trade_id``. Returns False if already present (reconnect dedup)."""
    with conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO exec_fills "
            "(trade_id, client_order_id, symbol, side, qty, px, commission, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_id, client_order_id, symbol, side, qty, px, commission, ts))
    return cur.rowcount == 1


def upsert_order(conn: sqlite3.Connection, *, client_order_id: str, venue: str, symbol: str,
                 side: int, qty: float, order_type: str, status: str, price: float | None = None,
                 trigger_price: float | None = None, venue_order_id: str | None = None,
                 filled_qty: float = 0.0, avg_fill_px: float = 0.0, updated_ts: int = 0) -> None:
    """Insert-or-replace the order snapshot row (fast cold-start state)."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO exec_orders "
            "(client_order_id, venue, symbol, side, qty, order_type, price, trigger_price, "
            " status, venue_order_id, filled_qty, avg_fill_px, updated_ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (client_order_id, venue, symbol, side, qty, order_type, price, trigger_price,
             status, venue_order_id, filled_qty, avg_fill_px, updated_ts))


def load_orders(conn: sqlite3.Connection) -> list[dict]:
    """All order snapshot rows as dicts (cold-start rebuild)."""
    cur = conn.execute(
        "SELECT client_order_id, venue, symbol, side, qty, order_type, price, trigger_price, "
        "status, venue_order_id, filled_qty, avg_fill_px, updated_ts FROM exec_orders")
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
