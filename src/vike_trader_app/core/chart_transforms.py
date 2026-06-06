"""Pure chart-style transforms — turn a time-series of OHLC ``Bar``s into alternative renderings.

Qt-free and deterministic (the UI layer renders the output). Two families:

- **Same-length** (``heikin_ashi``): one output bar per input bar, so time/index alignment is
  preserved and on-chart indicators/markers keep working.
- **Non-time** (``renko``, ``range_bars``, ``line_break``, ``kagi``, ``point_and_figure``): a
  *different* number of synthetic units on an ordinal axis. Each unit still carries the timestamp
  of the source bar that produced it, so the chart's time axis can label it — but these styles are
  inherently non-linear in time (the labels are approximate by nature).

The implementations are close-based and intentionally simple (no tick data, no exotic variants) —
enough for faithful chart *styling*, not a tick-accurate trading engine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Bar


def auto_box(bars: list[Bar]) -> float:
    """A sensible default box/reversal size: the average true range (ATR) of the series.

    Falls back to 0.5% of the last price when the ATR is degenerate (flat series).
    """
    if not bars:
        return 0.0
    if len(bars) < 2:
        return (bars[0].high - bars[0].low) or abs(bars[0].close) * 0.005 or 1.0
    trs = []
    prev = bars[0].close
    for b in bars[1:]:
        trs.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
        prev = b.close
    atr = sum(trs) / len(trs) if trs else 0.0
    if atr <= 0:
        atr = abs(bars[-1].close) * 0.005 or 1.0
    return atr


def heikin_ashi(bars: list[Bar]) -> list[Bar]:
    """Heikin-Ashi smoothed candles — one per input bar (1:1, time-aligned).

    HA close = (O+H+L+C)/4; HA open = midpoint of the *previous* HA open/close (seeded from the
    first bar's O/C); HA high/low extend to include the HA body.
    """
    out: list[Bar] = []
    prev_o = prev_c = None
    for b in bars:
        ha_close = (b.open + b.high + b.low + b.close) / 4.0
        ha_open = (b.open + b.close) / 2.0 if prev_o is None else (prev_o + prev_c) / 2.0
        ha_high = max(b.high, ha_open, ha_close)
        ha_low = min(b.low, ha_open, ha_close)
        out.append(Bar(ts=b.ts, open=ha_open, high=ha_high, low=ha_low, close=ha_close, volume=b.volume))
        prev_o, prev_c = ha_open, ha_close
    return out


def renko(bars: list[Bar], box_size: float | None = None) -> list[Bar]:
    """Close-based Renko bricks. A brick forms each time the close moves a full ``box_size``.

    Each brick is a ``Bar`` whose open/close are the brick's price bounds (high/low equal them —
    Renko bricks have no wick), stamped with the timestamp of the bar that completed it.
    """
    if not bars:
        return []
    box = box_size if (box_size and box_size > 0) else auto_box(bars)
    bricks: list[Bar] = []
    last = bars[0].close
    for b in bars:
        c = b.close
        while c >= last + box:
            o, cl = last, last + box
            bricks.append(Bar(ts=b.ts, open=o, high=cl, low=o, close=cl, volume=0.0))
            last = cl
        while c <= last - box:
            o, cl = last, last - box
            bricks.append(Bar(ts=b.ts, open=o, high=o, low=cl, close=cl, volume=0.0))
            last = cl
    return bricks


def range_bars(bars: list[Bar], range_size: float | None = None) -> list[Bar]:
    """Approximate range bars from OHLC: emit a new bar each time price travels ``range_size``.

    Built from OHLC (not ticks), so the path within a source bar is approximated; direction is
    taken from the source bar's close vs the running anchor.
    """
    if not bars:
        return []
    rng = range_size if (range_size and range_size > 0) else auto_box(bars)
    out: list[Bar] = []
    anchor = bars[0].open
    hi, lo, ts = bars[0].high, bars[0].low, bars[0].ts
    for b in bars:
        hi, lo, ts = max(hi, b.high), min(lo, b.low), b.ts
        while hi - lo >= rng:
            if b.close >= anchor:
                o, cl = lo, lo + rng
            else:
                o, cl = hi, hi - rng
            out.append(Bar(ts=ts, open=o, high=max(o, cl), low=min(o, cl), close=cl, volume=0.0))
            anchor = cl
            hi = lo = cl
    return out


def line_break(bars: list[Bar], n: int = 3) -> list[Bar]:
    """N-line break (default 3): a new line forms only when the close exceeds the extreme of the
    last ``n`` lines (up) or breaks below it (down); otherwise no line is drawn.

    Each line is a ``Bar`` block spanning the prior line's edge to the breakout close.
    """
    if len(bars) < 2:
        return []
    blocks: list[dict] = []
    prev_close = bars[0].close
    for b in bars[1:]:
        c = b.close
        if not blocks:
            if c > prev_close:
                blocks.append({"bottom": prev_close, "top": c, "up": True, "ts": b.ts})
            elif c < prev_close:
                blocks.append({"bottom": c, "top": prev_close, "up": False, "ts": b.ts})
            prev_close = c
            continue
        ref = blocks[-n:] if len(blocks) >= n else blocks
        hi = max(x["top"] for x in ref)
        lo = min(x["bottom"] for x in ref)
        last = blocks[-1]
        if c > hi:
            blocks.append({"bottom": last["top"], "top": c, "up": True, "ts": b.ts})
            prev_close = c
        elif c < lo:
            blocks.append({"bottom": c, "top": last["bottom"], "up": False, "ts": b.ts})
            prev_close = c
    return [
        Bar(ts=x["ts"],
            open=(x["bottom"] if x["up"] else x["top"]),
            high=x["top"], low=x["bottom"],
            close=(x["top"] if x["up"] else x["bottom"]), volume=0.0)
        for x in blocks
    ]


@dataclass(frozen=True)
class KagiResult:
    """A Kagi line. ``prices`` are the turning-point levels (vertices); ``thick[i]`` is the
    yang/yin (thick/thin) flag for the segment between vertex i and i+1. ``bars`` is a per-vertex
    ``Bar`` proxy (o=h=l=c=price) so the chart's time axis / autoscale can consume it.
    """

    bars: list
    prices: list
    thick: list


def kagi(bars: list[Bar], reversal: float | None = None) -> KagiResult:
    """Kagi line — a continuous line that reverses only after a counter-move of ``reversal``.

    Thickness flips (yang↔yin) when a segment breaks beyond the prior turning extreme — the
    standard shoulder/waist rule, approximated on closes.
    """
    if not bars:
        return KagiResult([], [], [])
    rev = reversal if (reversal and reversal > 0) else auto_box(bars)
    prices = [bars[0].close]
    tss = [bars[0].ts]
    direction = 0  # +1 rising, -1 falling
    for b in bars[1:]:
        c, cur = b.close, prices[-1]
        if direction == 0:
            if abs(c - cur) >= rev:
                direction = 1 if c > cur else -1
                prices.append(c)
                tss.append(b.ts)
        elif direction > 0:
            if c > cur:
                prices[-1], tss[-1] = c, b.ts
            elif cur - c >= rev:
                prices.append(c)
                tss.append(b.ts)
                direction = -1
        else:
            if c < cur:
                prices[-1], tss[-1] = c, b.ts
            elif c - cur >= rev:
                prices.append(c)
                tss.append(b.ts)
                direction = 1
    thick = []
    for i in range(1, len(prices)):
        prior = prices[: i - 1] if i >= 2 else prices[:1]
        if prices[i] > prices[i - 1]:
            thick.append(prices[i] >= max(prior) if prior else True)
        else:
            thick.append(prices[i] <= min(prior) if prior else False)
    proxy = [Bar(ts=tss[i], open=prices[i], high=prices[i], low=prices[i], close=prices[i], volume=0.0)
             for i in range(len(prices))]
    return KagiResult(proxy, prices, thick)


@dataclass(frozen=True)
class PnFColumn:
    """One Point & Figure column of X's (up) or O's (down) between ``bottom`` and ``top``."""

    up: bool
    bottom: float
    top: float
    ts: int


@dataclass(frozen=True)
class PnFResult:
    """Point & Figure chart. ``columns`` are the X/O columns; ``bars`` is a per-column ``Bar``
    proxy (open=bottom, close=top) for the axis/autoscale; ``box`` is the box size used.
    """

    bars: list
    columns: list
    box: float


def point_and_figure(bars: list[Bar], box_size: float | None = None, reversal: int = 3) -> PnFResult:
    """Close-based Point & Figure. Extend the current column by whole boxes; switch column (X↔O)
    only on a ``reversal``-box counter-move.
    """
    if not bars:
        return PnFResult([], [], 0.0)
    box = box_size if (box_size and box_size > 0) else auto_box(bars)

    def fl(p: float) -> float:
        return math.floor(p / box) * box

    cols: list[dict] = []
    cur: dict | None = None
    for b in bars:
        c = b.close
        if cur is None:
            cur = {"up": True, "top": fl(c), "bottom": fl(c), "ts": b.ts}
            continue
        if cur["up"]:
            if c >= cur["top"] + box:
                cur["top"], cur["ts"] = fl(c), b.ts
            elif c <= cur["top"] - reversal * box:
                cols.append(cur)
                cur = {"up": False, "top": cur["top"] - box, "bottom": fl(c), "ts": b.ts}
        else:
            if c <= cur["bottom"] - box:
                cur["bottom"], cur["ts"] = fl(c), b.ts
            elif c >= cur["bottom"] + reversal * box:
                cols.append(cur)
                cur = {"up": True, "top": fl(c), "bottom": cur["bottom"] + box, "ts": b.ts}
    if cur is not None:
        cols.append(cur)
    columns = [PnFColumn(up=x["up"], bottom=x["bottom"], top=x["top"], ts=x["ts"]) for x in cols]
    proxy = [Bar(ts=c.ts, open=c.bottom, high=c.top, low=c.bottom, close=c.top, volume=0.0) for c in columns]
    return PnFResult(proxy, columns, box)
