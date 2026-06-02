"""Local Parquet store for bars, read/written with Polars.

Two layouts coexist: the legacy single file ``<symbol>/<interval>.parquet`` and the Phase-2b
append-only layout — month partitions under ``<symbol>/<interval>/<YYYY-MM>.parquet``. The
``*_series`` helpers hide that split: ``read_series`` merges both; ``append_series`` writes only
the month(s) that changed (and migrates a legacy file into partitions on first append).
"""

from pathlib import Path

import polars as pl

from ..core.model import Bar
from .partition import partition_by_month


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
        parts.extend(read_bars_parquet(legacy))
    d = series_dir(root, symbol, interval)
    if d.is_dir():
        for f in sorted(d.glob("*.parquet")):
            parts.extend(read_bars_parquet(f))
    return _merge([], parts) if parts else []


def _migrate_legacy(root: str, symbol: str, interval: str) -> None:
    """Split a legacy single file into month partitions, then remove it (one-time, on first append)."""
    legacy = legacy_path(root, symbol, interval)
    if not legacy.exists():
        return
    d = series_dir(root, symbol, interval)
    for month, month_bars in partition_by_month(read_bars_parquet(legacy)).items():
        path = d / f"{month}.parquet"
        existing = read_bars_parquet(path) if path.exists() else []
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
        existing = read_bars_parquet(path) if path.exists() else []
        write_bars_parquet(_merge(existing, month_bars), path)
