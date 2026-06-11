"""DataSets — named symbol collections (Wealth-Lab's first-class concept).

A DataSet bundles a list of symbols with an optional default provider + interval, so the Data
Manager can download/update a whole universe ("Crypto Majors", "My FX") in one action.

Why a database: per the project rule, runtime state lives in the app's SQLite store, never in
loose JSON files. Each DataSet is one ``datasets`` row keyed by **name** (the natural key — the
legacy ``<slug>.json`` filename was only ever a stand-in for it), with the dataclass as a JSON
payload: the exact dict codec of ``_dataset_to_dict``/``_dataset_from_dict``, one format, no
drift. The legacy ``<root>/datasets/*.json`` dir is swept into the DB once (user edits
preserved via INSERT OR IGNORE — the DB wins on a re-sweep), then deleted; a file that fails to
parse is left in place — DataSets are user-authored. The DB is derived from ``root``
(``<root>/db/vike_trader_app.sqlite``), so ``root`` stays the only seam callers/tests need
(see :mod:`.state_db`, mirroring :mod:`.instrument_db`'s broker profiles).
"""

import json
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from . import state_db

log = logging.getLogger(__name__)

_TABLE = "datasets"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    name    TEXT PRIMARY KEY,
    payload TEXT NOT NULL  -- the DataSet as JSON (the _dataset_to_dict codec)
);
"""


@dataclass(frozen=True)
class DateRange:
    """A membership window [start_ts, end_ts] in epoch ms. end_ts None = open-ended (still a member)."""

    start_ts: int
    end_ts: int | None = None

    def contains(self, ts: int) -> bool:
        return ts >= self.start_ts and (self.end_ts is None or ts <= self.end_ts)


@dataclass
class DataSet:
    """A named collection of symbols + how to fetch them by default."""

    name: str
    symbols: list[str] = field(default_factory=list)
    provider: str | None = None   # None = Auto (infer crypto/forex), else an explicit provider
    interval: str = "1m"
    ranges: dict[str, list[DateRange]] = field(default_factory=dict)
    benchmark: str = ""  # optional benchmark symbol (e.g. "SPY", "BTCUSDT"); "" = equal-weight default

    def is_dynamic(self) -> bool:
        """True when any symbol has explicit membership windows (WealthLab dynamic DataSet)."""
        return any(self.ranges.values())

    def active_at(self, symbol: str, ts: int) -> bool:
        """Whether ``symbol`` is a member at ``ts``. A symbol with no ranges is always active."""
        windows = self.ranges.get(symbol)
        if not windows:
            return True
        return any(w.contains(ts) for w in windows)


def parse_symbols(text: str) -> list[str]:
    """Split a free-text symbol blob (commas / whitespace / newlines) → upper, deduped, ordered."""
    out: list[str] = []
    for tok in re.split(r"[\s,;]+", text.strip()):
        s = tok.strip().upper()
        if s and s not in out:
            out.append(s)
    return out


def provider_group(d: "DataSet") -> str | None:
    """The tree node a DataSet belongs under: 'Binance' (crypto) or 'Dukascopy' (FX), or None.

    A linked provider decides directly (crypto providers -> Binance node, dukascopy/yahoo -> Dukascopy
    node). Unlinked sets are inferred from their first symbol; an empty unlinked set has no group.
    """
    from .sources import CRYPTO_PROVIDERS, is_forex_symbol

    if d.provider in CRYPTO_PROVIDERS:
        return "Binance"
    if d.provider in ("dukascopy", "yahoo"):
        return "Dukascopy"
    if not d.symbols:
        return None
    return "Dukascopy" if is_forex_symbol(d.symbols[0]) else "Binance"


def datasets_dir(root: str) -> Path:
    """Where the legacy per-DataSet JSON files lived — read only by the one-time sweep."""
    return Path(root) / "datasets"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dataset"


def dataset_path(root: str, name: str) -> Path:
    """The legacy JSON file for ``name`` — kept for the sweep and historical callers."""
    return datasets_dir(root) / f"{_slug(name)}.json"


def _dataset_to_dict(d: DataSet) -> dict:
    return {
        "name": d.name,
        "symbols": list(d.symbols),
        "provider": d.provider,
        "interval": d.interval,
        "ranges": {
            sym: [{"start_ts": w.start_ts, "end_ts": w.end_ts} for w in windows]
            for sym, windows in d.ranges.items()
        },
        "benchmark": d.benchmark,
    }


def _dataset_from_dict(data: dict) -> DataSet:
    ranges = {
        sym: [DateRange(w["start_ts"], w.get("end_ts")) for w in windows]
        for sym, windows in (data.get("ranges") or {}).items()
    }
    return DataSet(
        name=data["name"],
        symbols=list(data.get("symbols", [])),
        provider=data.get("provider"),
        interval=data.get("interval", "1m"),
        ranges=ranges,
        benchmark=data.get("benchmark", ""),
    )


def _open_db(root: str) -> sqlite3.Connection:
    """Open the app DB with the datasets table ensured, sweeping the legacy dir in once."""
    db = state_db.app_db_path(root)
    conn = state_db.connect(db, _SCHEMA)
    state_db.sweep_once(conn, db, _TABLE, datasets_dir(root),
                        lambda c: _sweep_legacy_dir(c, datasets_dir(root)))
    return conn


def _sweep_legacy_dir(conn: sqlite3.Connection, d: Path) -> None:
    """Import ``<root>/datasets/*.json`` (DB rows win), delete handled files + the emptied dir.

    A file that fails to parse is left in place (and logged) so the user can recover it by
    hand — DataSets are user-authored, not a refetchable cache.
    """
    if not d.is_dir():
        return
    moved = 0
    for f in sorted(d.glob("*.json")):
        try:
            data = _dataset_from_dict(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, OSError):
            log.warning("datasets migration: leaving unreadable %s in place", f)
            continue
        with conn:
            conn.execute("INSERT OR IGNORE INTO datasets (name, payload) VALUES (?, ?)",
                         (data.name, json.dumps(_dataset_to_dict(data))))
        try:
            f.unlink()  # imported or superseded either way — the DB is truth now
            moved += 1
        except OSError as exc:
            log.warning("datasets migration: could not delete %s (%s)", f, exc)
    try:  # leave no empty legacy dir behind (best-effort; skipped files keep it alive)
        d.rmdir()
    except OSError:
        pass
    if moved:
        log.info("datasets migration: moved %d DataSet file(s) from %s into the app DB",
                 moved, d)


def save_dataset(d: DataSet, root: str) -> None:
    with closing(_open_db(root)) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO datasets (name, payload) VALUES (?, ?)",
                     (d.name, json.dumps(_dataset_to_dict(d))))


def load_dataset(name: str, root: str) -> DataSet | None:
    with closing(_open_db(root)) as conn:
        row = conn.execute("SELECT payload FROM datasets WHERE name = ?", (name,)).fetchone()
    if row is None:
        return None
    return _dataset_from_dict(json.loads(row[0]))


def list_datasets(root: str) -> list[str]:
    with closing(_open_db(root)) as conn:
        rows = conn.execute("SELECT name FROM datasets ORDER BY name").fetchall()
    return [r[0] for r in rows]


def delete_dataset(name: str, root: str) -> None:
    with closing(_open_db(root)) as conn, conn:
        conn.execute("DELETE FROM datasets WHERE name = ?", (name,))


def preset_datasets() -> dict[str, DataSet]:
    """Built-in example DataSets so the panel isn't empty on first open."""
    return {
        "Crypto Majors": DataSet(
            "Crypto Majors",
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"],
            provider=None, interval="1m",
        ),
        "FX Majors": DataSet(
            "FX Majors",
            ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD"],
            provider="dukascopy", interval="1h",
        ),
    }


def ensure_examples(root: str) -> list[str]:
    """Store any example DataSet not already present; return names written (idempotent)."""
    written = []
    with closing(_open_db(root)) as conn:
        for name, d in preset_datasets().items():
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO datasets (name, payload) VALUES (?, ?)",
                    (d.name, json.dumps(_dataset_to_dict(d))))
            if cur.rowcount:
                written.append(name)
    return sorted(written)
