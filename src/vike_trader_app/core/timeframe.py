"""Multi-timeframe support: synthesize higher timeframes from a fine base stream.

A strategy backtests on ONE base resolution (e.g. 1m). Higher timeframes are
derived from the base bars by pure OHLCV aggregation (jesse-style 1m base), so
MTF is look-ahead-safe by construction: a coarse bar only becomes visible once
its window has fully elapsed (backtrader's "deliver on complete" rule).
"""

from .model import Bar

_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}


def parse_timeframe(tf: str) -> int:
    """Convert a timeframe string like ``"1m"``/``"4h"``/``"1d"`` to milliseconds."""
    if len(tf) < 2 or tf[-1] not in _UNIT_MS or not tf[:-1].isdigit():
        raise ValueError(f"bad timeframe {tf!r} (expected e.g. '1m', '4h', '1d')")
    return int(tf[:-1]) * _UNIT_MS[tf[-1]]


def resample(bars, target_ms: int):
    """Aggregate fine ``bars`` into coarse ``Bar``s aligned to epoch windows of ``target_ms``.

    Returns one coarse bar per window in chronological order. The final window may be
    partial — visibility/look-ahead handling is the engine's job, not this function's.
    """
    out: list[Bar] = []
    cur_start: int | None = None
    o = h = l = c = 0.0
    vol = 0.0
    for b in bars:
        start = b.ts - b.ts % target_ms
        if start != cur_start:
            if cur_start is not None:
                out.append(Bar(ts=cur_start, open=o, high=h, low=l, close=c, volume=vol))
            cur_start = start
            o, h, l, c, vol = b.open, b.high, b.low, b.close, b.volume
        else:
            h = max(h, b.high)
            l = min(l, b.low)
            c = b.close
            vol += b.volume
    if cur_start is not None:
        out.append(Bar(ts=cur_start, open=o, high=h, low=l, close=c, volume=vol))
    return out
