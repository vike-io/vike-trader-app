"""Qt-free helpers for the Data Manager panel — display formatting + on-disk size.

Kept out of the Qt widget (``datamanager.py``) so it's unit-testable, matching the
``watchlist_data`` / ``chartdata`` convention. The widget renders the strings these return.
"""

from datetime import datetime, timezone

from ..data import parquet_source as ps
from ..data.quality import validate_bars


def human_size(n: int) -> str:
    """A compact human byte size, e.g. ``512 B`` / ``1.5 KB`` / ``5.0 MB`` / ``3.0 GB``."""
    if n < 1024:
        return f"{n} B"
    size = float(n)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024
        if size < 1024:
            return f"{size:.1f} {unit}"
    return f"{size / 1024:.1f} PB"


def human_ts(ms: int) -> str:
    """Epoch-ms (UTC) as ``YYYY-MM-DD HH:MM``."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def row_cells(info, pinned: bool, size_bytes: int) -> list[str]:
    """One Data Manager table row from a ``DatasetInfo``: symbol, tf, #bars, from, to, size, pin."""
    return [
        info.symbol, info.interval, f"{info.n_bars:,}",
        human_ts(info.start_ts), human_ts(info.end_ts),
        human_size(size_bytes), "📌" if pinned else "",
    ]


def quality_summary(bars: list, interval_ms: int) -> str:
    """A human report of a series' data quality — gaps, ordering, and OHLC anomalies.

    Wraps ``quality.validate_bars`` (which surfaces interior gaps + bad/duplicate timestamps +
    invalid OHLC) into a one-or-more-line string for the Data Manager's Inspect/log view.
    """
    if not bars:
        return "no data"
    problems = validate_bars(bars, interval_ms)
    if not problems:
        return f"clean — {len(bars):,} bars, no gaps or anomalies"
    return f"{len(bars):,} bars — issues:\n" + "\n".join(f"  • {p}" for p in problems)


def series_size_bytes(root: str, symbol: str, interval: str) -> int:
    """Total on-disk bytes for a cached series — legacy single file + all month partitions."""
    total = 0
    legacy = ps.legacy_path(root, symbol, interval)
    if legacy.exists():
        total += legacy.stat().st_size
    d = ps.series_dir(root, symbol, interval)
    if d.is_dir():
        total += sum(f.stat().st_size for f in d.glob("*.parquet"))
    return total
