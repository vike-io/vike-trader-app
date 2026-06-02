"""Pure helpers for the auto-updating chart + connection watchdog (Qt-free, network-free).

The main chart is a one-shot load today: after ``_load_symbol`` paints, nothing re-fetches
the displayed symbol, so the candles freeze while the "● BINANCE" badge implies a live link.
These two transforms are the testable core of the fix; the Qt timer/wiring in ``ui/app.py``
stays thin (matching the polling_feed / watchlist_data "pure logic, thin I/O" convention).

``merge_live_bars`` folds a tiny "latest bars" fetch (whose final element is the still-forming
candle) into the displayed series — replace the last bar when it ticks, append when a candle
rolls over, dedupe/sort otherwise — and reports what changed so the caller can decide whether
to repaint. ``feed_health`` classifies the feed as ``"live" | "stale" | "down"`` from the
newest bar's age and the consecutive-fetch-failure streak, driving the badge colour/text and
the poll cadence (steady when live, slow when stale, exponential backoff when down).
"""

from ..core.model import Bar


def merge_live_bars(existing: list[Bar], fetched: list[Bar]) -> tuple[list[Bar], int, bool]:
    """Fold ``fetched`` (ends with the forming candle) into ``existing``.

    Returns ``(merged, appended, replaced_last)``:
      - ``merged``: union deduped by ``ts`` (``fetched`` wins on a tie), sorted ascending.
      - ``appended``: how many bars are newer than the previous tail (closed-candle rollovers).
      - ``replaced_last``: True when the previously-last candle's ``ts`` came back with
        *different* values (the current candle ticked) — identical restatements don't count,
        so the caller can treat "nothing changed" as a no-op repaint.
    """
    existing = list(existing)
    if not fetched:
        return existing, 0, False

    last_ts = existing[-1].ts if existing else None
    by_ts: dict[int, Bar] = {b.ts: b for b in existing}
    replaced_last = False
    for b in fetched:
        prev = by_ts.get(b.ts)
        if last_ts is not None and b.ts == last_ts and prev is not None and b != prev:
            replaced_last = True
        by_ts[b.ts] = b

    merged = [by_ts[t] for t in sorted(by_ts)]
    appended = len(merged) if last_ts is None else sum(1 for t in by_ts if t > last_ts)
    return merged, appended, replaced_last


def feed_health(
    now: int,
    newest_ts: int | None,
    interval_ms: int,
    fail_streak: int,
    *,
    down_after: int = 3,
    stale_intervals: int = 2,
) -> str:
    """Classify the feed: ``"live" | "stale" | "down"``.

    ``down`` when fetches keep erroring (``fail_streak >= down_after``) — that's a real
    connection problem, independent of how fresh the last bar looks. Otherwise the newest
    bar's age decides: ``live`` while it's within ``stale_intervals`` intervals of ``now``
    (the forming candle is current), else ``stale`` (e.g. a quiet/closed market). No data
    yet (``newest_ts is None``) reads as ``stale`` unless the failure streak makes it ``down``.
    """
    if fail_streak >= down_after:
        return "down"
    if newest_ts is None:
        return "stale"
    age = now - newest_ts
    return "live" if age <= stale_intervals * interval_ms else "stale"


def closed_bars(bars: list[Bar], interval_ms: int, now: int) -> list[Bar]:
    """Drop a still-forming tail bar so backtests stay closed-bar-only (look-ahead-safe).

    The live fetch ends with the current candle, whose window hasn't elapsed yet
    (``now < ts + interval_ms``). It's fine to *display*, but a backtest/record run must
    exclude it — an incomplete final bar would leak the in-progress close. A bar whose
    window has fully elapsed is kept.
    """
    if bars and now < bars[-1].ts + interval_ms:
        return bars[:-1]
    return list(bars)


def live_fetch_window(
    last_ts: int | None,
    now: int,
    interval_ms: int,
    *,
    lookback: int = 5,
    max_bars: int = 1500,
) -> tuple[int, int]:
    """Return ``(start, now)`` for a live tick's fetch — gap-aware so a pause doesn't tear a hole.

    Steady polling fetches the last ``lookback`` bars (the forming candle + a couple closed
    ones for tick-replace). After a quiet stretch (e.g. a long Forward run that paused the
    updater) the window stretches back to bridge the gap — plus 2 bars of margin to re-fetch
    the last closed bar and the forming one — capped at ``max_bars`` so a huge catch-up stays
    bounded (anything older is healed by the cache on the next ``_load_symbol``).
    """
    if last_ts is None:
        return now - lookback * interval_ms, now
    needed = (now - last_ts) // interval_ms + 2
    bars = max(lookback, min(needed, max_bars))
    return now - bars * interval_ms, now
