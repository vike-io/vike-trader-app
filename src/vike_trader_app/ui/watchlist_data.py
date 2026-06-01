"""Qt-free helpers for the watchlist / cache-first symbol load.

Pure logic kept out of the Qt window (``MainWindow``) so it's unit-testable, matching the
``chartdata.py`` / ``dashboard_data.py`` convention. ``is_stale`` decides serve-cache-vs-top-up;
``quote_from_bars`` derives a watchlist quote (last close + 24h change).
"""

_DAY_MS = 86_400_000


def is_stale(last_ts: int | None, now_ms: int, fresh_ms: int) -> bool:
    """True when cached bars are missing or their newest bar is older than ``fresh_ms``.

    The cache-first load serves cached bars instantly only while *fresh*; a stale (or empty)
    cache must top up the recent gap from the network before display, so the latest bars
    actually appear. Freshness is decided purely by the right edge — depth of history is
    irrelevant. (Replaces the old depth-based ``covers`` check, which happily served
    deep-but-hours-stale history without ever fetching the missing recent bars.)
    """
    return last_ts is None or (now_ms - last_ts) > fresh_ms


def quote_from_bars(bars):
    """``(last_close, 24h_change_frac)`` for a watchlist quote, or None if no bars."""
    if not bars:
        return None
    last = bars[-1]
    cutoff = last.ts - _DAY_MS  # ~24h change reference
    ref = next((b for b in bars if b.ts >= cutoff), bars[0])
    chg = (last.close / ref.close - 1.0) if ref.close else 0.0
    return (last.close, chg)
