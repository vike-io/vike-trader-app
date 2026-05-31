"""Candlestick patterns — native, dependency-free.

Each pattern is a registered indicator over OHLC returning a ``list[int]`` aligned to the input:
``+100`` at a bullish pattern bar, ``-100`` bearish, ``0`` otherwise (non-directional patterns
like doji emit ``+100`` to mark presence). Definitions are standard textbook forms (not byte-
identical to TA-Lib's exact thresholds). A rolling average body (``_avg_body``) supplies the
"long/short body" context used by many patterns.
"""

from .base import indicator
from .overlap import sma

_CTX = 10  # bars of context for the rolling average body


def _body(o, c):
    return abs(c - o)


def _range(h, l):
    return h - l


def _upper(o, h, c):
    return h - max(o, c)


def _lower(o, l, c):
    return min(o, c) - l


def _is_white(o, c):
    return c > o


def _is_black(o, c):
    return c < o


def _avg_body(opens, closes, period=_CTX):
    """Rolling SMA of |close-open| (the 'average body' context), aligned, None warm-up."""
    bodies = [abs(closes[i] - opens[i]) for i in range(len(closes))]
    return sma(bodies, period)


def _is_doji(o, h, l, c, avg):
    """Body is tiny relative to the average body (<= 10%) and there is a real range."""
    return avg is not None and avg > 0 and _body(o, c) <= 0.1 * avg and _range(h, l) > 0


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["doji"])
def doji(opens, highs, lows, closes):
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        if _is_doji(opens[i], highs[i], lows[i], closes[i], avg[i]):
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["engulfing"])
def engulfing(opens, highs, lows, closes):
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        # bullish: prev black, curr white, curr body engulfs prev body
        if _is_black(po, pc) and _is_white(o, c) and c >= po and o <= pc:
            out[i] = 100
        # bearish: prev white, curr black, curr body engulfs prev body
        elif _is_white(po, pc) and _is_black(o, c) and o >= pc and c <= po:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["hammer"])
def hammer(opens, highs, lows, closes):
    n = len(closes)
    out = [0] * n
    for i in range(n):
        body = _body(opens[i], closes[i])
        rng = _range(highs[i], lows[i])
        if rng <= 0 or body <= 0:
            continue
        lower = _lower(opens[i], lows[i], closes[i])
        upper = _upper(opens[i], highs[i], closes[i])
        # small body (<=30% of range), long lower shadow (>=2x body), upper shadow <= body
        if body <= 0.3 * rng and lower >= 2 * body and upper <= body:
            out[i] = 100
    return out
