"""SQLite-backed history of backtest runs — your research ledger.

Stores each run's symbol/interval/strategy, the exact data window, and key stats so
past experiments survive closing the app and can be reopened from the History panel.
Uses stdlib sqlite3 (no extra dependency).
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from ..core.model import Bar

DEFAULT_PATH = "storage/db/vike_trader_app.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            INTEGER NOT NULL,
    symbol        TEXT    NOT NULL,
    interval      TEXT    NOT NULL,
    strategy      TEXT    NOT NULL,
    start_ts      INTEGER NOT NULL,
    end_ts        INTEGER NOT NULL,
    n_bars        INTEGER NOT NULL,
    net_return    REAL    NOT NULL,
    final_equity  REAL    NOT NULL,
    trades        INTEGER NOT NULL,
    win_rate      REAL    NOT NULL,
    profit_factor REAL    NOT NULL,
    max_drawdown  REAL    NOT NULL,
    sharpe        REAL    NOT NULL,
    params        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS forward_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_ts  INTEGER NOT NULL,
    symbol      TEXT    NOT NULL,
    interval    TEXT    NOT NULL,
    strategy    TEXT    NOT NULL,
    cash        REAL    NOT NULL,
    fee_rate    REAL    NOT NULL,
    params      TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'running'
);

-- Each closed live bar received by a forward run, so the run can be replayed
-- (re-seed + re-apply) after the app is closed and reopened.
CREATE TABLE IF NOT EXISTS forward_bars (
    run_id  INTEGER NOT NULL,
    ts      INTEGER NOT NULL,
    open    REAL    NOT NULL,
    high    REAL    NOT NULL,
    low     REAL    NOT NULL,
    close   REAL    NOT NULL,
    volume  REAL    NOT NULL,
    funding REAL,
    PRIMARY KEY (run_id, ts)
);
"""

# scalar columns, in insert order (params handled separately as JSON)
_COLS = [
    "ts",
    "symbol",
    "interval",
    "strategy",
    "start_ts",
    "end_ts",
    "n_bars",
    "net_return",
    "final_equity",
    "trades",
    "win_rate",
    "profit_factor",
    "max_drawdown",
    "sharpe",
]


@dataclass
class ForwardRunRecord:
    """A paper forward-test run: its config + lifecycle status (bars stored separately)."""

    created_ts: int
    symbol: str
    interval: str
    strategy: str
    cash: float
    fee_rate: float
    params: dict = field(default_factory=dict)
    status: str = "running"
    id: int | None = None


@dataclass
class RunRecord:
    """One saved backtest run (metadata + headline stats + the exact data window)."""

    ts: int
    symbol: str
    interval: str
    strategy: str
    start_ts: int
    end_ts: int
    n_bars: int
    net_return: float
    final_equity: float
    trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    sharpe: float
    params: dict = field(default_factory=dict)
    id: int | None = None


class Store:
    """A small SQLite wrapper for run history. Pass ``:memory:`` for an ephemeral DB."""

    def __init__(self, path: str = DEFAULT_PATH):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def save_run(self, rec: RunRecord) -> int:
        cols = [*_COLS, "params"]
        placeholders = ", ".join("?" for _ in cols)
        values = [getattr(rec, c) for c in _COLS] + [json.dumps(rec.params)]
        cur = self.conn.execute(
            f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders})", values
        )
        self.conn.commit()
        rec.id = cur.lastrowid
        return cur.lastrowid

    def list_runs(self, limit: int = 200) -> list[RunRecord]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def delete_run(self, run_id: int) -> None:
        self.conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        self.conn.commit()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM runs")
        self.conn.commit()

    # --- forward (paper) runs ---
    def create_forward_run(
        self, *, symbol, interval, strategy, cash, fee_rate, params, created_ts
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO forward_runs "
            "(created_ts, symbol, interval, strategy, cash, fee_rate, params, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'running')",
            (created_ts, symbol, interval, strategy, cash, fee_rate, json.dumps(params)),
        )
        self.conn.commit()
        return cur.lastrowid

    def append_forward_bar(self, run_id: int, bar: Bar) -> None:
        """Persist one received closed bar (idempotent: re-appending a ts replaces it)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO forward_bars "
            "(run_id, ts, open, high, low, close, volume, funding) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume, bar.funding),
        )
        self.conn.commit()

    def forward_bars(self, run_id: int) -> list[Bar]:
        rows = self.conn.execute(
            "SELECT ts, open, high, low, close, volume, funding "
            "FROM forward_bars WHERE run_id = ? ORDER BY ts ASC",
            (run_id,),
        ).fetchall()
        return [
            Bar(ts=r["ts"], open=r["open"], high=r["high"], low=r["low"],
                close=r["close"], volume=r["volume"], funding=r["funding"])
            for r in rows
        ]

    def set_forward_status(self, run_id: int, status: str) -> None:
        self.conn.execute("UPDATE forward_runs SET status = ? WHERE id = ?", (status, run_id))
        self.conn.commit()

    def list_forward_runs(self, limit: int = 200) -> list[ForwardRunRecord]:
        rows = self.conn.execute(
            "SELECT * FROM forward_runs ORDER BY created_ts DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            ForwardRunRecord(
                id=r["id"], created_ts=r["created_ts"], symbol=r["symbol"],
                interval=r["interval"], strategy=r["strategy"], cash=r["cash"],
                fee_rate=r["fee_rate"], params=json.loads(r["params"]), status=r["status"],
            )
            for r in rows
        ]

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _to_record(row) -> RunRecord:
        return RunRecord(
            id=row["id"],
            ts=row["ts"],
            symbol=row["symbol"],
            interval=row["interval"],
            strategy=row["strategy"],
            start_ts=row["start_ts"],
            end_ts=row["end_ts"],
            n_bars=row["n_bars"],
            net_return=row["net_return"],
            final_equity=row["final_equity"],
            trades=row["trades"],
            win_rate=row["win_rate"],
            profit_factor=row["profit_factor"],
            max_drawdown=row["max_drawdown"],
            sharpe=row["sharpe"],
            params=json.loads(row["params"]),
        )
