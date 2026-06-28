"""Cache-first bar loading — the seam shared by the shell and (Phase 2) chart documents.

Extracted from ``MainWindow._load_symbol`` so every chart-bearing surface loads the same way:
a *fresh* cached tail (newest bar within ``FRESH_MS``) paints instantly with zero network;
otherwise ``get_bars`` tops up just the missing recent gap (it is incremental). A failed
top-up falls back to whatever is cached rather than returning nothing.

Reads are thread-safe (the parquet primitive uses per-call DuckDB connections); writes still
run on the main thread. Imports of the data layer happen inside the function so tests can
monkeypatch ``data.catalog.Catalog`` / ``ui.dataload.get_bars``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..data.cache import get_bars
from ..data.sources import select_source

_DAY_MS = 86_400_000
FRESH_MS = 5 * 60_000  # cache-first: reuse cached bars if the last one is this fresh
# Days of history pulled per timeframe so the range selector (1D..5Y) has enough bars.
INTERVAL_LOOKBACK = {"1m": 7, "3m": 7, "5m": 10, "15m": 21, "30m": 30,
                     "1h": 90, "2h": 120, "4h": 240, "1d": 1825, "1w": 3650}
_DEFAULT_LOOKBACK_DAYS = 7


def lookback_start(interval: str, now_ms: int) -> int:
    """The default history-window start (ms) for ``interval`` — the cache-first lookback used by
    load_symbol_bars, exposed so an off-thread top-up (LiveHub) can size a cold-load gap range."""
    return now_ms - INTERVAL_LOOKBACK.get(interval, _DEFAULT_LOOKBACK_DAYS) * _DAY_MS


@dataclass
class LoadResult:
    """Outcome of a symbol load. ``bars`` may be cached data even on error (stale fallback)."""

    bars: list = field(default_factory=list)
    stale_fallback: bool = False   # fetch failed; serving the cached (possibly stale) tail
    error: str = ""                # non-empty when the network top-up failed

    @property
    def ok(self) -> bool:
        return bool(self.bars)


def load_symbol_bars(symbol: str, interval: str, now_ms: int, *,
                     progress=None, network: bool = True) -> LoadResult:
    """Load ``symbol``/``interval`` cache-first (see module docstring).

    ``network=False`` serves the cache only — used for restoring background chart documents
    lazily (they top up when focused) without a startup network storm.
    """
    from ..data.catalog import Catalog

    from .watchlist_data import is_stale

    start = now_ms - INTERVAL_LOOKBACK.get(interval, _DEFAULT_LOOKBACK_DAYS) * _DAY_MS
    cached = Catalog().query(symbol, interval, start, now_ms)
    if cached and not is_stale(cached[-1].ts, now_ms, FRESH_MS):
        return LoadResult(list(cached))
    if not network:
        return LoadResult(list(cached) if cached else [])
    try:
        bars = get_bars(symbol, interval, start, now_ms, progress=progress,
                        fetcher=select_source(symbol).fetch_bars_range)
        return LoadResult(list(bars))
    except Exception as exc:  # noqa: BLE001 - network/load failure -> cached fallback
        if cached:
            return LoadResult(list(cached), stale_fallback=True, error=str(exc))
        return LoadResult([], error=str(exc))
