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


def source_label(symbol: str) -> str:
    """Human datasource for a cached series, inferred from Auto routing (the provider isn't
    persisted per series). Forex pairs route to Dukascopy (deep history, stitched with Yahoo for
    the recent edge); everything else is crypto via Binance. A series force-downloaded from another
    exchange (bybit/okx/…) can't be distinguished after the fact, so this shows the default route."""
    from ..data.sources import is_forex_symbol

    return "Dukascopy" if is_forex_symbol(symbol) else "Binance"


def instrument_label(spec) -> str:
    """A compact cell for the Data Manager's Instrument column, e.g. ``crypto · tick 0.01``."""
    return f"{spec.asset_class} · tick {spec.tick_size:g}"


def instrument_detail(spec, profile_name: str) -> str:
    """A one-line spec dump for the Inspect log — asset, tick/pip/step, contract, decimals, profile."""
    return (f"{spec.asset_class} | tick {spec.tick_size:g} · pip {spec.pip_size:g} · "
            f"step {spec.volume_step:g} · contract {spec.contract_size:g} · {spec.decimals}dp "
            f"(profile: {profile_name})")


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


def inactive_candidates(infos, *, zero_bars: bool = True, last_before_ms: int | None = None):
    """Cached series to prune: ``(symbol, interval)`` for each dead/stale ``DatasetInfo``.

    A series is a candidate when it has zero bars (``zero_bars``) OR — only if ``last_before_ms`` is
    given — its last bar is older than that cutoff (``end_ts < last_before_ms``). Order is preserved.
    """
    out = []
    for info in infos:
        dead = zero_bars and info.n_bars == 0
        stale = (last_before_ms is not None and info.n_bars > 0
                 and info.end_ts < last_before_ms)
        if dead or stale:
            out.append((info.symbol, info.interval))
    return out


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
