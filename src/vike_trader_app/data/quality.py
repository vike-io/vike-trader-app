"""Bar-series quality checks — pure DETECTION over ``list[Bar]`` (no refetch/repair).

``find_gaps`` surfaces interior holes a head/tail check (``cache.missing_ranges``) misses;
``validate_bars`` returns human-readable problems for an OHLCV series. The repair/refetch
policy is deferred to a design spec — these functions only report, they never mutate.
"""

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
