"""Month-partition helpers for the append-only Parquet base (Phase 2b).

The base is stored as monthly Parquet partitions — ``<symbol>/<interval>/<YYYY-MM>.parquet`` —
so an incremental fetch only rewrites the month(s) that changed instead of the whole series
(the old single-file layout rewrote everything on every top-up). These two pure functions are
the partitioning core; the file I/O lives in ``parquet_source``/``cache``.
"""

from datetime import datetime, timezone

from ..core.model import Bar


def month_key(ts_ms: int) -> str:
    """The ``YYYY-MM`` partition a timestamp (epoch ms, UTC) belongs to."""
    d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def partition_by_month(bars: list[Bar]) -> dict[str, list[Bar]]:
    """Group ``bars`` into ``{YYYY-MM: [bars]}`` buckets, preserving order within each month."""
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault(month_key(b.ts), []).append(b)
    return out
