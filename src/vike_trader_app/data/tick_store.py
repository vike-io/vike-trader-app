"""Day-partitioned parquet store for raw ticks (the bid/ask source of truth).

Layout: ``<root>/<symbol>/<kind>/<YYYY-MM-DD>.parquet`` where kind is "quotes" or
"trades". A day write merges with any existing day file and dedupes on the FULL row
(so re-fetching a day is idempotent, but distinct same-ts trades are kept). Mirrors
``parquet_source``'s corrupt-partition quarantine: an unreadable day degrades to a
refillable gap rather than crashing the read.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from ..core.ticks import QuoteTick, TradeTick

log = logging.getLogger(__name__)


def _day_key(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d")


def _kind_dir(root: str, symbol: str, kind: str) -> Path:
    return Path(root) / symbol / kind


def _read_partition(path: Path) -> list[dict]:
    try:
        return pl.read_parquet(path).to_dicts()
    except (pl.exceptions.PolarsError, OSError) as e:
        log.warning("skipping unreadable tick partition %s: %s", path, e)
        return []


def _merge_rows(existing: list[dict], new: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for r in sorted(existing + new, key=lambda r: r["ts"]):
        key = tuple(sorted(r.items()))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _write(rows: list[dict], root: str, symbol: str, kind: str) -> None:
    if not rows:
        return
    d = _kind_dir(root, symbol, kind)
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(_day_key(r["ts"]), []).append(r)
    for day, day_rows in by_day.items():
        path = d / f"{day}.parquet"
        existing = _read_partition(path) if path.exists() else []
        merged = _merge_rows(existing, day_rows)
        path.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(merged).write_parquet(path)


def _read(root: str, symbol: str, kind: str, start_ms: int, end_ms: int) -> list[dict]:
    d = _kind_dir(root, symbol, kind)
    if not d.is_dir():
        return []
    start_day, end_day = _day_key(start_ms), _day_key(end_ms)
    out: list[dict] = []
    for f in sorted(d.glob("*.parquet")):
        if start_day <= f.stem <= end_day:
            out.extend(_read_partition(f))
    return [r for r in sorted(out, key=lambda r: r["ts"]) if start_ms <= r["ts"] <= end_ms]


def write_quotes(ticks: list[QuoteTick], root: str, symbol: str) -> None:
    _write([{"ts": t.ts, "bid": t.bid, "ask": t.ask,
             "bid_size": t.bid_size, "ask_size": t.ask_size} for t in ticks],
           root, symbol, "quotes")


def read_quotes(root: str, symbol: str, start_ms: int, end_ms: int) -> list[QuoteTick]:
    return [QuoteTick(**r) for r in _read(root, symbol, "quotes", start_ms, end_ms)]


def write_trades(ticks: list[TradeTick], root: str, symbol: str) -> None:
    _write([{"ts": t.ts, "price": t.price, "size": t.size,
             "is_buyer_maker": t.is_buyer_maker} for t in ticks],
           root, symbol, "trades")


def read_trades(root: str, symbol: str, start_ms: int, end_ms: int) -> list[TradeTick]:
    return [TradeTick(**r) for r in _read(root, symbol, "trades", start_ms, end_ms)]
