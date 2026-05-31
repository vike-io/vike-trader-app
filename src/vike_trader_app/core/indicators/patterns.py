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


# ---------------------------------------------------------------------------
# Task 2: Single-bar patterns (18)
# ---------------------------------------------------------------------------


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["inverted_hammer"])
def inverted_hammer(opens, highs, lows, closes):
    """Small body near the low, long upper shadow (≥2×body), little lower shadow → +100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        body = _body(opens[i], closes[i])
        rng = _range(highs[i], lows[i])
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(opens[i], highs[i], closes[i])
        lower = _lower(opens[i], lows[i], closes[i])
        # small body (≤30% of range), long upper shadow (≥2×body), small lower (≤body)
        if body <= 0.3 * rng and upper >= 2 * body and lower <= body:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["hanging_man"])
def hanging_man(opens, highs, lows, closes):
    """Hammer geometry (small body near top, long lower shadow) — bearish signal → -100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        body = _body(opens[i], closes[i])
        rng = _range(highs[i], lows[i])
        if rng <= 0 or body <= 0:
            continue
        lower = _lower(opens[i], lows[i], closes[i])
        upper = _upper(opens[i], highs[i], closes[i])
        # same geometry as hammer but signals bearish reversal
        if body <= 0.3 * rng and lower >= 2 * body and upper <= body:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["shooting_star"])
def shooting_star(opens, highs, lows, closes):
    """Inverted hammer geometry — bearish shooting star → -100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        body = _body(opens[i], closes[i])
        rng = _range(highs[i], lows[i])
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(opens[i], highs[i], closes[i])
        lower = _lower(opens[i], lows[i], closes[i])
        # same geometry as inverted_hammer but signals bearish
        if body <= 0.3 * rng and upper >= 2 * body and lower <= body:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["dragonfly_doji"])
def dragonfly_doji(opens, highs, lows, closes):
    """Doji with open≈close≈high, long lower shadow → +100 (bullish)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # doji body, upper shadow tiny (≤10% range), lower shadow ≥50% range
        if body <= 0.1 * a and upper <= 0.1 * rng and lower >= 0.5 * rng:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["gravestone_doji"])
def gravestone_doji(opens, highs, lows, closes):
    """Doji with open≈close≈low, long upper shadow → -100 (bearish)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # doji body, lower shadow tiny (≤10% range), upper shadow ≥50% range
        if body <= 0.1 * a and lower <= 0.1 * rng and upper >= 0.5 * rng:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["longlegged_doji"])
def longlegged_doji(opens, highs, lows, closes):
    """Doji with long upper AND lower shadows (each ≥30% of range) → +100 (presence)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # doji body, both shadows long
        if body <= 0.1 * a and upper >= 0.3 * rng and lower >= 0.3 * rng:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["rickshaw_man"])
def rickshaw_man(opens, highs, lows, closes):
    """Long-legged doji with body near the middle of the range → +100 (presence)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # body midpoint should be near range midpoint (within 25% of range)
        body_mid = (max(o, c) + min(o, c)) / 2
        range_mid = (h + l) / 2
        if (body <= 0.1 * a and upper >= 0.3 * rng and lower >= 0.3 * rng
                and abs(body_mid - range_mid) <= 0.25 * rng):
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["takuri"])
def takuri(opens, highs, lows, closes):
    """Dragonfly doji with an exceptionally long lower shadow (≥3× upper shadow or body) → +100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # doji body, tiny upper shadow, exceptionally long lower (≥70% of range)
        if body <= 0.1 * a and upper <= 0.1 * rng and lower >= 0.7 * rng:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["marubozu"])
def marubozu(opens, highs, lows, closes):
    """Body ≈ full range (shadows ≤5% of range); white → +100, black → -100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # both shadows tiny relative to range
        if upper <= 0.05 * rng and lower <= 0.05 * rng:
            if _is_white(o, c):
                out[i] = 100
            elif _is_black(o, c):
                out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["closing_marubozu"])
def closing_marubozu(opens, highs, lows, closes):
    """No shadow on the CLOSE side; white: no upper shadow; black: no lower shadow → ±100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        if _is_white(o, c) and upper <= 0.05 * rng:
            out[i] = 100
        elif _is_black(o, c) and lower <= 0.05 * rng:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["spinning_top"])
def spinning_top(opens, highs, lows, closes):
    """Small body, upper and lower shadows each > body → +100 (presence)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # small body (≤30% of range), both shadows larger than body
        if body <= 0.3 * rng and upper > body and lower > body:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["high_wave"])
def high_wave(opens, highs, lows, closes):
    """Very small body with very long upper AND lower shadows (each ≥3×body) → +100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        # body ≤15% of range, both shadows very long (≥3×body each)
        if body <= 0.15 * rng and upper >= 3 * body and lower >= 3 * body:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["long_line"])
def long_line(opens, highs, lows, closes):
    """Body ≥1.3× avg body (a long candle); white → +100, black → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, c = opens[i], closes[i]
        body = _body(o, c)
        if body >= 1.3 * a:
            if _is_white(o, c):
                out[i] = 100
            elif _is_black(o, c):
                out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["short_line"])
def short_line(opens, highs, lows, closes):
    """Body ≤0.5× avg body (a short candle) with small range → +100 (presence)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        # body ≤0.5×avg and range also small (≤avg): short, compact candle
        if body <= 0.5 * a and rng <= a:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["belt_hold"])
def belt_hold(opens, highs, lows, closes):
    """White opens at its low (no lower shadow) long body → +100;
    black opens at its high (no upper shadow) long body → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body <= 0:
            continue
        lower = _lower(o, l, c)
        upper = _upper(o, h, c)
        # white belt hold: opens at low, long body
        if _is_white(o, c) and lower <= 0.05 * rng and body >= a:
            out[i] = 100
        # black belt hold: opens at high, long body
        elif _is_black(o, c) and upper <= 0.05 * rng and body >= a:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["opening_marubozu"])
def opening_marubozu(opens, highs, lows, closes):
    """No shadow on the OPEN side; white: no lower shadow; black: no upper shadow → ±100."""
    n = len(closes)
    out = [0] * n
    for i in range(n):
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body <= 0:
            continue
        upper = _upper(o, h, c)
        lower = _lower(o, l, c)
        if _is_white(o, c) and lower <= 0.05 * rng:
            out[i] = 100
        elif _is_black(o, c) and upper <= 0.05 * rng:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["doji_star"])
def doji_star(opens, highs, lows, closes):
    """Doji that gaps away from a prior long body; gap up after white → +100, gap down after black → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        # current bar must be a doji
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        body = _body(o, c)
        rng = _range(h, l)
        if rng <= 0 or body > 0.1 * a:
            continue
        # prior bar must have a long body
        po, pc = opens[i - 1], closes[i - 1]
        prev_body = _body(po, pc)
        if prev_body < a:
            continue
        # gap up: doji entire bar above prior close (after white prior)
        if _is_white(po, pc) and l > pc:
            out[i] = 100
        # gap down: doji entire bar below prior close (after black prior)
        elif _is_black(po, pc) and h < pc:
            out[i] = -100
    return out
