"""SQLite-backed history of backtest runs — your research ledger.

Stores each run's symbol/interval/strategy, the exact data window, and key stats so
past experiments survive closing the app and can be reopened from the History panel.
Uses stdlib sqlite3 (no extra dependency).
"""

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

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
