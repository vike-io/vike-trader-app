"""Local Parquet cache for bars — gives the engine real history depth.

``get_bars`` returns the requested range, reading from a per-symbol/timeframe Parquet
file and fetching only the parts not already cached (incremental fill). Cached history
is assumed contiguous; gaps before/after the cached span are fetched and merged.
"""

from pathlib import Path

from ..core.model import Bar
from .binance_source import fetch_bars_range, interval_ms
from .parquet_source import read_bars_parquet, write_bars_parquet

DEFAULT_ROOT = "storage/parquet"


def cache_path(root: str, symbol: str, interval: str) -> Path:
    """Where a symbol/timeframe's bars live: ``<root>/<symbol>/<interval>.parquet``."""
    return Path(root) / symbol / f"{interval}.parquet"


def merge_bars(existing: list[Bar], new: list[Bar]) -> list[Bar]:
    """Combine two bar lists, dedup by timestamp (``new`` wins), sorted ascending."""
    by_ts: dict[int, Bar] = {b.ts: b for b in existing}
    for b in new:
        by_ts[b.ts] = b
    return [by_ts[t] for t in sorted(by_ts)]


def covered_range(bars: list[Bar]) -> tuple[int, int] | None:
    """The ``(first_ts, last_ts)`` covered by ``bars``, or None if empty."""
    if not bars:
        return None
    return (bars[0].ts, bars[-1].ts)


def slice_bars(bars: list[Bar], start: int, end: int) -> list[Bar]:
    """Bars with timestamp in ``[start, end]`` (inclusive)."""
    return [b for b in bars if start <= b.ts <= end]


def missing_ranges(
    cached: tuple[int, int] | None, requested: tuple[int, int], step: int
) -> list[tuple[int, int]]:
    """Ranges to fetch so ``cached`` covers ``requested`` (keeps the span contiguous)."""
    r0, r1 = requested
    if cached is None:
        return [(r0, r1)]
    c0, c1 = cached
    gaps: list[tuple[int, int]] = []
    if r0 < c0:
        gaps.append((r0, c0 - step))  # extend earlier
    if r1 > c1:
        gaps.append((c1 + step, r1))  # extend later
    return gaps


def get_bars(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    root: str = DEFAULT_ROOT,
    fetcher=fetch_bars_range,
    progress=None,
) -> list[Bar]:
    """Return bars for ``[start_ms, end_ms]``, fetching only what isn't already cached."""
    path = cache_path(root, symbol, interval)
    cached = read_bars_parquet(path) if path.exists() else []
    step = interval_ms(interval)

    fetched: list[Bar] = []
    for s, e in missing_ranges(covered_range(cached), (start_ms, end_ms), step):
        fetched.extend(fetcher(symbol, interval, s, e, progress=progress))

    if fetched:
        cached = merge_bars(cached, fetched)
        write_bars_parquet(cached, path)

    return slice_bars(cached, start_ms, end_ms)
