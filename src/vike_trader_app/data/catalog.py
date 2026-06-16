"""Queryable data catalog over the local Parquet cache (nautilus-style).

Indexes ``<root>/<symbol>/<interval>.parquet`` so callers can discover what data
exists and pull a time-range slice without knowing file paths:
``Catalog(root).query("BTCUSDT", "1m", start, end)``. Zero-config — it simply reads
whatever the cache layer (``data/cache.py``) has already fetched/stored.
"""

from dataclasses import dataclass
from pathlib import Path

from .cache import DEFAULT_ROOT, slice_bars
from .parquet_source import read_series, read_series_since


@dataclass
class DatasetInfo:
    """Metadata for one cached (symbol, interval) dataset."""

    symbol: str
    interval: str
    n_bars: int
    start_ts: int
    end_ts: int
    path: str


class Catalog:
    """Discover and query the local Parquet datasets under ``root``."""

    def __init__(self, root: str = DEFAULT_ROOT):
        self.root = Path(root)

    def symbols(self) -> list[str]:
        """Symbols that have at least one cached interval, sorted.

        ``**/*.parquet`` matches both the legacy ``<symbol>/<interval>.parquet`` and the
        partitioned ``<symbol>/<interval>/<YYYY-MM>.parquet`` layouts.
        """
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and any(p.glob("**/*.parquet")))

    def intervals(self, symbol: str) -> list[str]:
        """Cached intervals for ``symbol``, sorted — legacy file stems + partition dirs."""
        d = self.root / symbol
        if not d.is_dir():
            return []
        out = {p.stem for p in d.glob("*.parquet")}  # legacy single files
        out |= {sub.name for sub in d.iterdir() if sub.is_dir() and any(sub.glob("*.parquet"))}
        return sorted(out)

    def info(self, symbol: str, interval: str) -> DatasetInfo | None:
        """Metadata for one dataset, or None if it isn't cached."""
        bars = read_series(str(self.root), symbol, interval)
        if not bars:
            return None
        return DatasetInfo(symbol, interval, len(bars), bars[0].ts, bars[-1].ts,
                           str(self.root / symbol / interval))

    def list_datasets(self) -> list[DatasetInfo]:
        """Every cached dataset's metadata, sorted by (symbol, interval)."""
        out: list[DatasetInfo] = []
        for symbol in self.symbols():
            for interval in self.intervals(symbol):
                ds = self.info(symbol, interval)
                if ds is not None:
                    out.append(ds)
        return out

    def query(self, symbol: str, interval: str, start: int | None = None, end: int | None = None):
        """Bars for ``symbol``/``interval`` in ``[start, end]`` (inclusive); ``[]`` if absent.

        When a lower bound ``start`` is given we read only the month partitions that can contain
        ``ts >= start`` (``read_series_since``) instead of decoding the entire multi-year series
        just to slice the tail — the live quote tick / startup price fill / doc load all pass a
        recent ``start``, so this is the hot path. The result is identical: ``slice_bars`` drops
        ``ts < start`` anyway, so pre-pruning those partitions changes nothing. Falls back to the
        full read when ``start is None`` (an end-only or unbounded query can't lower-prune)."""
        if start is not None:
            bars = read_series_since(str(self.root), symbol, interval, start)
        else:
            bars = read_series(str(self.root), symbol, interval)
        if not bars:
            return []
        if start is None and end is None:
            return bars
        lo = start if start is not None else bars[0].ts
        hi = end if end is not None else bars[-1].ts
        return slice_bars(bars, lo, hi)
