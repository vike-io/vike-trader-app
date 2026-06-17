"""Local Parquet store for bars, read/written with Polars.

Two layouts coexist: the legacy single file ``<symbol>/<interval>.parquet`` and the Phase-2b
append-only layout — month partitions under ``<symbol>/<interval>/<YYYY-MM>.parquet``. The
``*_series`` helpers hide that split: ``read_series`` merges both; ``append_series`` writes only
the month(s) that changed (and migrates a legacy file into partitions on first append).
"""

import logging
import shutil
from pathlib import Path

import polars as pl

from ..core.model import Bar
from .partition import month_key, partition_by_month

log = logging.getLogger(__name__)


def bars_to_dataframe(bars: list[Bar]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }
    )


def dataframe_to_bars(df: pl.DataFrame) -> list[Bar]:
    return [
        Bar(
            ts=r["ts"],
            open=r["open"],
            high=r["high"],
            low=r["low"],
            close=r["close"],
            volume=r["volume"],
        )
        for r in df.iter_rows(named=True)
    ]


def write_bars_parquet(bars: list[Bar], path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bars_to_dataframe(bars).write_parquet(path)


def read_bars_parquet(path) -> list[Bar]:
    return dataframe_to_bars(pl.read_parquet(path))


def _read_partition(path) -> list[Bar]:
    """Read one parquet partition, QUARANTINING a corrupt/truncated file instead of aborting the
    caller. The live app killed mid-write can leave a partial ``<...>.parquet`` whose decode raises
    (Polars ``ComputeError``); swallowing it here degrades that one month to a refillable gap — the
    next fetch re-downloads it — rather than crashing. Used by BOTH the read helpers (read_series*)
    and the write helpers (append/migrate/truncate): a corrupt month is unreadable anyway, so on
    append it is overwritten with fresh data (self-heal) rather than left as a permanent crash."""
    try:
        return read_bars_parquet(path)
    except (pl.exceptions.PolarsError, OSError) as e:
        log.warning("skipping unreadable parquet partition %s: %s", path, e)
        return []


# --- partitioned series layout (Phase 2b) ---

def series_dir(root: str, symbol: str, interval: str) -> Path:
    """The month-partition directory ``<root>/<symbol>/<interval>/``."""
    return Path(root) / symbol / interval


def legacy_path(root: str, symbol: str, interval: str) -> Path:
    """The pre-Phase-2b single file ``<root>/<symbol>/<interval>.parquet``."""
    return Path(root) / symbol / f"{interval}.parquet"


def _merge(existing: list[Bar], new: list[Bar]) -> list[Bar]:
    """Dedup by ts (``new`` wins on a tie), sorted ascending."""
    by_ts = {b.ts: b for b in existing}
    for b in new:
        by_ts[b.ts] = b
    return [by_ts[t] for t in sorted(by_ts)]


def series_exists(root: str, symbol: str, interval: str) -> bool:
    return legacy_path(root, symbol, interval).exists() or series_dir(root, symbol, interval).is_dir()


def read_series(root: str, symbol: str, interval: str) -> list[Bar]:
    """All bars for ``(symbol, interval)`` — merges the legacy single file with month partitions."""
    parts: list[Bar] = []
    legacy = legacy_path(root, symbol, interval)
    if legacy.exists():
        parts.extend(_read_partition(legacy))
    d = series_dir(root, symbol, interval)
    if d.is_dir():
        for f in sorted(d.glob("*.parquet")):
            parts.extend(_read_partition(f))
    return _merge([], parts) if parts else []


def read_series_since(root: str, symbol: str, interval: str, start_ms: int) -> list[Bar]:
    """Bars with ``ts >= start_ms``, reading only the month partitions that can contain them.

    Month files are named ``YYYY-MM`` (lexical order == chronological), so any partition whose
    month is before ``start_ms``'s month is skipped — an incremental refresh reads the tail, not
    the whole base. A legacy single file (not partitioned) is read in full, then filtered.
    """
    start_month = month_key(start_ms)
    parts: list[Bar] = []
    legacy = legacy_path(root, symbol, interval)
    if legacy.exists():
        parts.extend(_read_partition(legacy))
    d = series_dir(root, symbol, interval)
    if d.is_dir():
        for f in sorted(d.glob("*.parquet")):
            if f.stem >= start_month:
                parts.extend(_read_partition(f))
    return [b for b in _merge([], parts) if b.ts >= start_ms]


def _migrate_legacy(root: str, symbol: str, interval: str) -> None:
    """Split a legacy single file into month partitions, then remove it (one-time, on first append)."""
    legacy = legacy_path(root, symbol, interval)
    if not legacy.exists():
        return
    d = series_dir(root, symbol, interval)
    for month, month_bars in partition_by_month(_read_partition(legacy)).items():
        path = d / f"{month}.parquet"
        existing = _read_partition(path) if path.exists() else []
        write_bars_parquet(_merge(existing, month_bars), path)
    legacy.unlink()


def append_series(new_bars: list[Bar], root: str, symbol: str, interval: str) -> None:
    """Merge ``new_bars`` into the series, rewriting only the month partition(s) they fall in."""
    if not new_bars:
        return
    _migrate_legacy(root, symbol, interval)
    d = series_dir(root, symbol, interval)
    for month, month_bars in partition_by_month(new_bars).items():
        path = d / f"{month}.parquet"
        existing = _read_partition(path) if path.exists() else []   # corrupt month -> overwrite (self-heal)
        write_bars_parquet(_merge(existing, month_bars), path)


def truncate_series(root: str, symbol: str, interval: str, *,
                    before_ts: int | None = None, after_ts: int | None = None) -> int:
    """Delete cached bars with ``ts < before_ts`` and/or ``ts > after_ts``. Returns bars removed.

    Rewrites only the month partition(s) that actually change (atomic temp-then-replace), unlinking
    any partition emptied by the cut. A legacy single file is migrated to partitions first so the
    cut is partition-aware. ``before_ts``/``after_ts`` are inclusive bounds on what is KEPT
    (a bar at exactly ``before_ts`` or ``after_ts`` is kept).
    """
    if before_ts is None and after_ts is None:
        return 0
    _migrate_legacy(root, symbol, interval)
    d = series_dir(root, symbol, interval)
    if not d.is_dir():
        return 0

    def keep(ts: int) -> bool:
        return (before_ts is None or ts >= before_ts) and (after_ts is None or ts <= after_ts)

    removed = 0
    for path in sorted(d.glob("*.parquet")):
        bars = _read_partition(path)         # corrupt partition -> [] (skipped) rather than a crash
        kept = [b for b in bars if keep(b.ts)]
        if len(kept) == len(bars):
            continue                         # untouched partition
        removed += len(bars) - len(kept)
        if not kept:
            path.unlink()                    # whole month cut away
        else:
            tmp = path.with_suffix(".parquet.tmp")
            write_bars_parquet(kept, tmp)    # atomic: write temp then replace
            tmp.replace(path)
    return removed


def delete_series(root: str, symbol: str, interval: str) -> None:
    """Remove a cached series — the legacy single file and the whole month-partition directory."""
    legacy = legacy_path(root, symbol, interval)
    if legacy.exists():
        legacy.unlink()
    d = series_dir(root, symbol, interval)
    if d.is_dir():
        shutil.rmtree(d)
