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


# ---------------------------------------------------------------------------
# Task 3: Two-bar patterns (16)
# ---------------------------------------------------------------------------


def _is_marubozu(o, h, l, c):
    """True if both shadows are ≤5% of range (open/close-side near extremes)."""
    rng = _range(h, l)
    if rng <= 0:
        return False
    return _upper(o, h, c) <= 0.05 * rng and _lower(o, l, c) <= 0.05 * rng


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["harami"])
def harami(opens, highs, lows, closes):
    """Current body inside prev body, opposite colour.
    Prev black / curr white → +100 (bullish reversal).
    Prev white / curr black → -100 (bearish reversal).
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        prev_hi = max(po, pc)
        prev_lo = min(po, pc)
        curr_hi = max(o, c)
        curr_lo = min(o, c)
        # current body must be strictly inside prev body
        if curr_hi >= prev_hi or curr_lo <= prev_lo:
            continue
        if _is_black(po, pc) and _is_white(o, c):
            out[i] = 100
        elif _is_white(po, pc) and _is_black(o, c):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["harami_cross"])
def harami_cross(opens, highs, lows, closes):
    """Harami where the current bar is a doji → ±100 by prior colour."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        po, pc = opens[i - 1], closes[i - 1]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        # current bar must be a doji (tiny body)
        if _body(o, c) > 0.1 * a:
            continue
        prev_hi = max(po, pc)
        prev_lo = min(po, pc)
        # doji open/close within prev body
        doji_mid = (o + c) / 2
        if doji_mid >= prev_hi or doji_mid <= prev_lo:
            continue
        if _is_black(po, pc):
            out[i] = 100
        elif _is_white(po, pc):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["piercing"])
def piercing(opens, highs, lows, closes):
    """Prev black, curr white opens below prev low, closes above prev midpoint
    (but below prev open) → +100.
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_black(po, pc):
            continue
        if not _is_white(o, c):
            continue
        midpoint = (po + pc) / 2
        # curr opens below prev low, closes above midpoint but below prev open
        if o < pl and c > midpoint and c < po:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["dark_cloud_cover"])
def dark_cloud_cover(opens, highs, lows, closes):
    """Prev white, curr black opens above prev high, closes below prev midpoint
    (but above prev open) → -100.
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_white(po, pc):
            continue
        if not _is_black(o, c):
            continue
        midpoint = (po + pc) / 2
        # curr opens above prev high, closes below midpoint but above prev open
        if o > ph and c < midpoint and c > po:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["counterattack"])
def counterattack(opens, highs, lows, closes):
    """Opposite-colour bodies that close at approximately the same price.
    Curr white (prev black) → +100; curr black (prev white) → -100.
    Tolerance: |curr_close - prev_close| ≤ 3% of avg body.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if abs(c - pc) > 0.03 * a + 1e-9:
            continue
        # both must have meaningful bodies
        if _body(po, pc) < 0.3 * a or _body(o, c) < 0.3 * a:
            continue
        if _is_black(po, pc) and _is_white(o, c):
            out[i] = 100
        elif _is_white(po, pc) and _is_black(o, c):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["meeting_lines"])
def meeting_lines(opens, highs, lows, closes):
    """Like counterattack: opposite-colour candles whose closes meet at ≈same price → ±100.
    Slightly tighter tolerance (≤2% of avg body) than counterattack.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if abs(c - pc) > 0.02 * a + 1e-9:
            continue
        if _body(po, pc) < 0.3 * a or _body(o, c) < 0.3 * a:
            continue
        if _is_black(po, pc) and _is_white(o, c):
            out[i] = 100
        elif _is_white(po, pc) and _is_black(o, c):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["separating_lines"])
def separating_lines(opens, highs, lows, closes):
    """Same colour as prior, current opens at prior open continuing trend.
    Two whites with same open → +100; two blacks with same open → -100.
    Tolerance: |curr_open - prev_open| ≤ 1% of avg body.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if abs(o - po) > 0.01 * a + 1e-9:
            continue
        if _is_white(po, pc) and _is_white(o, c):
            out[i] = 100
        elif _is_black(po, pc) and _is_black(o, c):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["matching_low"])
def matching_low(opens, highs, lows, closes):
    """Two black candles with equal closes → +100 (bullish reversal at support).
    Tolerance: |curr_close - prev_close| ≤ 1% of avg body.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_black(po, pc) or not _is_black(o, c):
            continue
        if abs(c - pc) <= 0.01 * a + 1e-9:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["on_neck"])
def on_neck(opens, highs, lows, closes):
    """Prev black (downtrend), curr white opens below prev low, closes at ≈prev low → -100 (continuation).
    Tolerance: |curr_close - prev_low| ≤ 3% of prev body.
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_black(po, pc) or not _is_white(o, c):
            continue
        prev_body = _body(po, pc)
        if prev_body <= 0:
            continue
        # curr opens below prev low, closes near prev low
        if o < pl and abs(c - pl) <= 0.03 * prev_body:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["in_neck"])
def in_neck(opens, highs, lows, closes):
    """Prev black, curr white opens below prev low, closes slightly into prev body (near prev close) → -100.
    Close is just above prev close (small penetration ≤ 15% of prev body).
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_black(po, pc) or not _is_white(o, c):
            continue
        prev_body = _body(po, pc)
        if prev_body <= 0:
            continue
        # curr opens below prev low, closes slightly into prev body (just above prev close)
        if o < pl and c > pc and (c - pc) <= 0.15 * prev_body:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["thrusting"])
def thrusting(opens, highs, lows, closes):
    """Prev black, curr white opens below prev low, closes into prev body but below midpoint → -100."""
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_black(po, pc) or not _is_white(o, c):
            continue
        prev_body = _body(po, pc)
        if prev_body <= 0:
            continue
        midpoint = (po + pc) / 2
        # opens below prev low, closes into body (above prev close) but below midpoint
        if o < pl and c > pc and c < midpoint:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["kicking"])
def kicking(opens, highs, lows, closes):
    """Two marubozu of opposite colour with a gap between them.
    White after black with gap up → +100; black after white with gap down → -100.
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if not _is_marubozu(po, ph, pl, pc) or not _is_marubozu(o, h, l, c):
            continue
        if _is_black(po, pc) and _is_white(o, c) and o > pc:
            out[i] = 100
        elif _is_white(po, pc) and _is_black(o, c) and o < pc:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["kicking_by_length"])
def kicking_by_length(opens, highs, lows, closes):
    """Kicking pattern, signal determined by the longer marubozu's colour."""
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if not _is_marubozu(po, ph, pl, pc) or not _is_marubozu(o, h, l, c):
            continue
        # must be a kicking pattern (opposite colours, gap between them)
        is_gap_up = _is_black(po, pc) and _is_white(o, c) and o > pc
        is_gap_down = _is_white(po, pc) and _is_black(o, c) and o < pc
        if not (is_gap_up or is_gap_down):
            continue
        prev_body = _body(po, pc)
        curr_body = _body(o, c)
        if curr_body >= prev_body:
            # longer is curr
            out[i] = 100 if _is_white(o, c) else -100
        else:
            # longer is prev
            out[i] = 100 if _is_white(po, pc) else -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["homing_pigeon"])
def homing_pigeon(opens, highs, lows, closes):
    """Two black candles, second is harami-inside the first → +100 (bullish reversal)."""
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, pc = opens[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if not _is_black(po, pc) or not _is_black(o, c):
            continue
        # second (curr) body must be inside first (prev) body
        prev_hi = max(po, pc)
        prev_lo = min(po, pc)
        curr_hi = max(o, c)
        curr_lo = min(o, c)
        if curr_hi < prev_hi and curr_lo > prev_lo:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["gap_side_side_white"])
def gap_side_side_white(opens, highs, lows, closes):
    """Two white candles of similar size gapping the same direction → continuation +100.
    Curr white candle gaps above prev white close; body sizes within 50% of each other.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(1, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, h, l, c = opens[i], highs[i], lows[i], closes[i]
        if not _is_white(po, pc) or not _is_white(o, c):
            continue
        prev_body = _body(po, pc)
        curr_body = _body(o, c)
        if prev_body <= 0 or curr_body <= 0:
            continue
        # gap up: curr entirely above prev close
        if o > pc and abs(curr_body - prev_body) <= 0.5 * max(prev_body, curr_body):
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["tasuki_gap"])
def tasuki_gap(opens, highs, lows, closes):
    """Gap then an opposite-colour candle that stays within the gap → continuation ±100.
    Bullish (+100): prev white, curr black opens within prev body and closes above prev open.
    Bearish (-100): prev black, curr white opens within prev body and closes below prev open.
    """
    n = len(closes)
    out = [0] * n
    for i in range(1, n):
        po, ph, pl, pc = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o, c = opens[i], closes[i]
        if _is_white(po, pc) and _is_black(o, c):
            # upside tasuki: curr black opens within prev body, closes above prev open
            if min(po, pc) < o < max(po, pc) and c > po:
                out[i] = 100
        elif _is_black(po, pc) and _is_white(o, c):
            # downside tasuki: curr white opens within prev body, closes below prev open
            if min(po, pc) < o < max(po, pc) and c < po:
                out[i] = -100
    return out
