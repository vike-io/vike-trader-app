"""Bar-series quality checks — pure DETECTION over ``list[Bar]`` (no refetch/repair).

``find_gaps`` surfaces interior holes a head/tail check (``cache.missing_ranges``) misses;
``validate_bars`` returns human-readable problems for an OHLCV series. The repair/refetch
policy is deferred to a design spec — these functions only report, they never mutate.

``repair_bars`` is the repair half: it returns a cleaned copy + an audit log but never
mutates the originals (``Bar`` is frozen) and never performs any I/O.
"""

import math

from ..core.model import Bar


def find_gaps(bars: list[Bar], interval_ms: int) -> list[tuple[int, int]]:
    """Interior holes where consecutive bars are more than one ``interval_ms`` apart.

    Each entry is ``(gap_start_ts, gap_end_ts)`` = the missing range
    ``prev.ts + interval_ms`` .. ``next.ts - interval_ms``. Empty if contiguous or <2 bars.
    """
    gaps: list[tuple[int, int]] = []
    for prev, nxt in zip(bars, bars[1:]):
        if nxt.ts - prev.ts > interval_ms:
            gaps.append((prev.ts + interval_ms, nxt.ts - interval_ms))
    return gaps


def validate_bars(bars: list[Bar], interval_ms: int) -> list[str]:
    """Human-readable problems in an OHLCV series; empty list == clean."""
    problems: list[str] = []
    for prev, nxt in zip(bars, bars[1:]):
        if nxt.ts <= prev.ts:
            kind = "duplicated" if nxt.ts == prev.ts else "out of order"
            problems.append(f"timestamps not strictly increasing ({kind}) at ts={nxt.ts}")
    for b in bars:
        if b.high < max(b.open, b.close) or b.high < b.low:
            problems.append(f"high below open/close/low at ts={b.ts}")
        if b.low > min(b.open, b.close):
            problems.append(f"low above open/close at ts={b.ts}")
        if min(b.open, b.high, b.low, b.close) < 0 or b.volume < 0:
            problems.append(f"negative price or volume at ts={b.ts}")
    gaps = find_gaps(bars, interval_ms)
    if gaps:
        problems.append(f"bar spacing not equal to interval: {len(gaps)} interior gap(s)")
    return problems


def _is_bad_price(v: float) -> bool:
    """True when a price field is unusable: zero, negative, or NaN."""
    return v <= 0 or math.isnan(v)


def repair_bars(bars: list[Bar], interval_ms: int) -> tuple[list[Bar], list[str]]:  # noqa: ARG001
    """Return ``(repaired_bars, audit_log)``. Fixes, in order, recording each mutation in the log:

    1. Drop bars with non-increasing/duplicate timestamps (keep the LAST bar for a duplicate ts).
    2. Replace a zero / negative / NaN price field (o/h/l/c) with the previous bar's close (or,
       for the first bar, with the median of the bar's other valid prices).
    3. Clamp so ``high = max(o,h,l,c)`` and ``low = min(o,h,l,c)`` for each bar.
    4. Clamp a negative volume to 0.

    Bars already valid are passed through untouched. The log lines are human-readable, e.g.
    ``'ts=… zero close -> 101.5'`` / ``'ts=… high 99 below max(o,c) 101 -> 101'`` /
    ``'dropped duplicate ts=…'``.
    """
    audit: list[str] = []

    # --- Step 1: deduplicate / drop non-increasing timestamps (keep LAST per ts) ---
    # Walk forward: accumulate into an ordered dict keyed by ts; on a duplicate the later
    # value overwrites the earlier one (keep last), and we record the drop.
    seen: dict[int, Bar] = {}
    ordered_ts: list[int] = []
    prev_ts: int | None = None
    for b in bars:
        if prev_ts is not None and b.ts <= prev_ts:
            if b.ts == prev_ts:
                audit.append(f"dropped duplicate ts={b.ts} (kept later bar)")
                seen[b.ts] = b          # overwrite: keep last
            else:
                # out-of-order: skip entirely
                audit.append(f"dropped out-of-order ts={b.ts} (prev ts={prev_ts})")
                continue
        else:
            if b.ts not in seen:
                ordered_ts.append(b.ts)
            seen[b.ts] = b
            prev_ts = b.ts

    deduped: list[Bar] = [seen[t] for t in ordered_ts]

    # --- Steps 2–4: fix prices and volume bar by bar ---
    repaired: list[Bar] = []
    prev_close: float | None = None

    for b in deduped:
        o, h, lo, c = b.open, b.high, b.low, b.close
        v = b.volume

        # Step 2: replace bad price fields
        for field in ("open", "high", "low", "close"):
            val = {"open": o, "high": h, "low": lo, "close": c}[field]
            if _is_bad_price(val):
                if prev_close is not None:
                    replacement = prev_close
                else:
                    # first bar: use median of the bar's other valid prices
                    others = [x for x in [o, h, lo, c] if not _is_bad_price(x)]
                    if others:
                        others_sorted = sorted(others)
                        mid = len(others_sorted) // 2
                        replacement = (others_sorted[mid - 1] + others_sorted[mid]) / 2 \
                            if len(others_sorted) % 2 == 0 else others_sorted[mid]
                    else:
                        replacement = 0.0   # all four are bad: can't do better
                label = "NaN" if math.isnan(val) else ("zero" if val == 0 else "negative")
                audit.append(f"ts={b.ts} {label} {field} -> {replacement}")
                if field == "open":
                    o = replacement
                elif field == "high":
                    h = replacement
                elif field == "low":
                    lo = replacement
                else:
                    c = replacement

        # Step 3: clamp high/low so the OHLC box is valid
        true_high = max(o, h, lo, c)
        true_low = min(o, h, lo, c)
        if h != true_high:
            audit.append(f"ts={b.ts} high {h} below max(o,h,l,c) {true_high} -> {true_high}")
            h = true_high
        if lo != true_low:
            audit.append(f"ts={b.ts} low {lo} above min(o,h,l,c) {true_low} -> {true_low}")
            lo = true_low

        # Step 4: clamp negative volume
        if v < 0:
            audit.append(f"ts={b.ts} negative volume {v} -> 0")
            v = 0.0

        new_bar = Bar(ts=b.ts, open=o, high=h, low=lo, close=c, volume=v, funding=b.funding)
        repaired.append(new_bar)
        prev_close = c

    return repaired, audit
