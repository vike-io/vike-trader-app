"""Pin-to-precompute rollups (Phase 3): materialize a higher timeframe from the 1m base.

For a *pinned* timeframe (one queried so often that on-the-fly resampling is wasteful — e.g. a
hot chart over a multi-million-bar NASDAQ base), ``refresh_rollup`` materialises it into its own
partitioned series so reads serve it directly (via ``Catalog`` / ``DuckCatalog.get_or_derive``)
instead of re-resampling. The refresh is:

- **incremental** — it recomputes only from the *watermark* (the last materialised bucket), not
  the whole base;
- **watermark-aware** — that bucket is *reopened* (recomputed from all its base bars) in case it
  was still partial when last materialised;
- **idempotent** — buckets are written by ``append_series`` (dedup by ts), so re-running with no
  new base data leaves the rollup unchanged.

Aggregation reuses ``core.timeframe.resample`` (the canonical, byte-identical rule), so a rollup
and an on-the-fly derive always agree. Pin only timeframes the source doesn't serve natively —
a pinned interval shares the ``<symbol>/<interval>/`` series with any native fetch of it.
"""

import json
from pathlib import Path

from ..core.timeframe import parse_timeframe, resample
from .parquet_source import append_series, read_series, read_series_since


def rollup_refresh_start(watermark_ts: int | None, target_ms: int) -> int:
    """Epoch ms to recompute the rollup from: the start of the bucket holding ``watermark_ts``.

    None (no rollup yet) → 0, i.e. build from the beginning. Otherwise floor the watermark to its
    bucket boundary so that (possibly partial) last bucket is recomputed from all of its base bars.
    """
    if watermark_ts is None:
        return 0
    return watermark_ts - watermark_ts % target_ms


def refresh_rollup(root: str, symbol: str, interval: str, base: str = "1m") -> int:
    """Incrementally materialise ``interval`` for ``symbol`` from the ``base`` series.

    Returns the number of rollup bars (re)written this pass (0 if there's nothing to do). Rolling
    ``base`` into itself is a no-op.
    """
    if interval == base:
        return 0
    target_ms = parse_timeframe(interval)
    existing = read_series(root, symbol, interval)
    start = rollup_refresh_start(existing[-1].ts if existing else None, target_ms)
    base_bars = read_series_since(root, symbol, base, start)  # partition-pruned: reads only the tail
    if not base_bars:
        return 0
    rolled = resample(base_bars, target_ms)
    append_series(rolled, root, symbol, interval)  # dedup by ts -> reopens the last bucket, idempotent
    return len(rolled)


# --- pin registry: which (symbol, interval) series to keep precomputed ---------------------

def load_pins(path: str) -> list[list[str]]:
    """Load pinned ``[symbol, interval]`` pairs from ``path`` (``[]`` if the file is absent)."""
    p = Path(path)
    if not p.exists():
        return []
    return [list(pair) for pair in json.loads(p.read_text())]


def save_pins(path: str, pins: list) -> None:
    """Persist pinned ``(symbol, interval)`` pairs to ``path`` (deduped, sorted)."""
    uniq = sorted({(s, i) for s, i in pins})
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps([[s, i] for s, i in uniq]))


def refresh_pinned(root: str, pins: list) -> dict:
    """Refresh every pinned rollup; returns ``{"symbol/interval": bars_written}``."""
    return {f"{s}/{i}": refresh_rollup(root, s, i) for s, i in pins}
