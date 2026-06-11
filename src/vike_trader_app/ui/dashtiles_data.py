"""Qt-free data prep for the dashboard info tiles (Movers / P&L / Calendar / News).

Keeps the tile logic testable without Qt, matching the ``chartdata.py``/``dashboard_data.py``
convention. The tiles themselves (``dashtiles.py``) only render what these helpers return.
"""

from __future__ import annotations

from datetime import UTC, datetime

_DAY_MS = 86_400_000


def top_movers(prices: dict, n: int = 6) -> list[tuple[str, float, float]]:
    """Rank watchlist quotes by absolute 24h change.

    ``prices`` maps symbol -> (last_close, change_frac) — the same tuples the watchlist's
    ``set_prices`` consumes. Returns up to ``n`` rows of (symbol, last, change_frac), biggest
    movers first. Entries with a falsy quote are skipped."""
    rows = [(sym, q[0], q[1]) for sym, q in prices.items() if q]
    rows.sort(key=lambda r: abs(r[2]), reverse=True)
    return rows[:n]


def pnl_summary(equity_curve, final_equity: float | None = None) -> dict | None:
    """Account snapshot from an equity curve: initial / current equity, P&L, return %.

    ``final_equity`` overrides the curve's last point (a Result carries the authoritative
    final equity). None when there is nothing to show."""
    if not equity_curve:
        return None
    initial = equity_curve[0]
    final = equity_curve[-1] if final_equity is None else final_equity
    pnl = final - initial
    ret = (final / initial - 1.0) * 100.0 if initial else 0.0
    return {"initial": initial, "equity": final, "pnl": pnl, "ret_pct": ret}


def day_bounds_utc(now_ms: int) -> tuple[int, int]:
    """[start, end) epoch-ms bounds of the UTC day containing ``now_ms``."""
    day = datetime.fromtimestamp(now_ms / 1000, tz=UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = int(day.timestamp() * 1000)
    return start, start + _DAY_MS


def today_events(events, now_ms: int, n: int = 12) -> list:
    """Today's (UTC) calendar events, soonest first, high-importance first within a minute."""
    lo, hi = day_bounds_utc(now_ms)
    todays = [e for e in events if lo <= e.ts_utc < hi]
    todays.sort(key=lambda e: (e.ts_utc, -e.importance))
    return todays[:n]


def latest_headlines(items, n: int = 8) -> list:
    """Newest ``n`` NewsItems (descending publish time)."""
    return sorted(items, key=lambda i: i.published_ms, reverse=True)[:n]


def age_label(published_ms: int, now_ms: int) -> str:
    """Compact "3m" / "2h" / "5d" age caption for a headline."""
    mins = max(0, (now_ms - published_ms) // 60_000)
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"
