"""Queryable data catalog over the local Parquet cache (nautilus-style).

Indexes ``<root>/<symbol>/<interval>.parquet`` so callers can discover what data
exists and pull a time-range slice without knowing file paths:
``Catalog(root).query("BTCUSDT", "1m", start, end)``. Zero-config — it simply reads
whatever the cache layer (``data/cache.py``) has already fetched/stored.
"""

from dataclasses import dataclass
from pathlib import Path

from .cache import DEFAULT_ROOT, slice_bars
from .parquet_source import read_bars_parquet


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
        """Symbols that have at least one cached interval, sorted."""
        if not self.root.exists():
            return []
        return sorted(p.name for p in self.root.iterdir() if p.is_dir() and any(p.glob("*.parquet")))

    def intervals(self, symbol: str) -> list[str]:
        """Cached intervals for ``symbol`` (file stems), sorted."""
        d = self.root / symbol
        return sorted(p.stem for p in d.glob("*.parquet")) if d.exists() else []

    def info(self, symbol: str, interval: str) -> DatasetInfo | None:
        """Metadata for one dataset, or None if it isn't cached."""
        path = self.root / symbol / f"{interval}.parquet"
        if not path.exists():
            return None
        bars = read_bars_parquet(path)
        if not bars:
            return None
        return DatasetInfo(symbol, interval, len(bars), bars[0].ts, bars[-1].ts, str(path))

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
        """Bars for ``symbol``/``interval`` in ``[start, end]`` (inclusive); ``[]`` if absent."""
        path = self.root / symbol / f"{interval}.parquet"
        if not path.exists():
            return []
        bars = read_bars_parquet(path)
        if start is None and end is None:
            return bars
        lo = start if start is not None else (bars[0].ts if bars else 0)
        hi = end if end is not None else (bars[-1].ts if bars else 0)
        return slice_bars(bars, lo, hi)
