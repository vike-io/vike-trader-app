"""Consolidate ticks into Bars (the tick->bar step; generalizes timeframe.resample).

Quote bars carry the bucket's OPENING best bid/ask (what a next-open market order
fills against), OHLC from the mid, and ``volume`` = tick count. Trade bars take OHLC
from price and ``volume`` = summed trade size, with no bid/ask.
"""

from .model import Bar
from .ticks import QuoteTick, TradeTick


def consolidate_quotes(ticks: list[QuoteTick], step_ms: int) -> list[Bar]:
    out: list[Bar] = []
    cur: int | None = None
    o = h = l = c = 0.0
    bid = ask = 0.0
    n = 0
    for t in ticks:
        start = t.ts - t.ts % step_ms
        m = t.mid
        if start != cur:
            if cur is not None:
                out.append(Bar(ts=cur, open=o, high=h, low=l, close=c,
                               volume=float(n), bid=bid, ask=ask))
            cur = start
            o = h = l = c = m
            bid, ask = t.bid, t.ask  # opening quote of the new bucket
            n = 0
        else:
            h = max(h, m)
            l = min(l, m)
            c = m
        n += 1
    if cur is not None:
        out.append(Bar(ts=cur, open=o, high=h, low=l, close=c,
                       volume=float(n), bid=bid, ask=ask))
    return out


def consolidate_trades(ticks: list[TradeTick], step_ms: int) -> list[Bar]:
    out: list[Bar] = []
    cur: int | None = None
    o = h = l = c = 0.0
    vol = 0.0
    for t in ticks:
        start = t.ts - t.ts % step_ms
        if start != cur:
            if cur is not None:
                out.append(Bar(ts=cur, open=o, high=h, low=l, close=c, volume=vol))
            cur = start
            o = h = l = c = t.price
            vol = t.size
        else:
            h = max(h, t.price)
            l = min(l, t.price)
            c = t.price
            vol += t.size
    if cur is not None:
        out.append(Bar(ts=cur, open=o, high=h, low=l, close=c, volume=vol))
    return out
