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


# ---------------------------------------------------------------------------
# Task 4: Three-bar (and longer) patterns (27)
# ---------------------------------------------------------------------------


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["morning_star"])
def morning_star(opens, highs, lows, closes):
    """3-bar: long black, small-body star gapping down, long white closing into first body → +100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bar1: long black
        if not _is_black(o1, c1) or _body(o1, c1) < a:
            continue
        # bar2: small body star (body < 30% of avg), gaps down (entire bar below bar1 close)
        if _body(o2, c2) >= 0.3 * a or h2 >= c1:
            continue
        # bar3: long white closing above bar1 midpoint
        if not _is_white(o3, c3) or _body(o3, c3) < a:
            continue
        mid1 = (o1 + c1) / 2
        if c3 > mid1:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["evening_star"])
def evening_star(opens, highs, lows, closes):
    """3-bar: long white, small-body star gapping up, long black closing into first body → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bar1: long white
        if not _is_white(o1, c1) or _body(o1, c1) < a:
            continue
        # bar2: small body star, gaps up (entire bar above bar1 close)
        if _body(o2, c2) >= 0.3 * a or l2 <= c1:
            continue
        # bar3: long black closing below bar1 midpoint
        if not _is_black(o3, c3) or _body(o3, c3) < a:
            continue
        mid1 = (o1 + c1) / 2
        if c3 < mid1:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["morning_doji_star"])
def morning_doji_star(opens, highs, lows, closes):
    """Morning star where the star bar is a doji → +100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not _is_black(o1, c1) or _body(o1, c1) < a:
            continue
        # star must be a doji (body ≤ 10% avg) and gap down
        if _body(o2, c2) > 0.1 * a or h2 >= c1:
            continue
        if not _is_white(o3, c3) or _body(o3, c3) < a:
            continue
        mid1 = (o1 + c1) / 2
        if c3 > mid1:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["evening_doji_star"])
def evening_doji_star(opens, highs, lows, closes):
    """Evening star where the star bar is a doji → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not _is_white(o1, c1) or _body(o1, c1) < a:
            continue
        # star must be a doji and gap up
        if _body(o2, c2) > 0.1 * a or l2 <= c1:
            continue
        if not _is_black(o3, c3) or _body(o3, c3) < a:
            continue
        mid1 = (o1 + c1) / 2
        if c3 < mid1:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["three_white_soldiers"])
def three_white_soldiers(opens, highs, lows, closes):
    """3 consecutive long white candles, each opening within prior body, closing near high → +100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # all three must be long white candles
        if not (_is_white(o1, c1) and _is_white(o2, c2) and _is_white(o3, c3)):
            continue
        if _body(o1, c1) < 0.7 * a or _body(o2, c2) < 0.7 * a or _body(o3, c3) < 0.7 * a:
            continue
        # bar2 opens within bar1 body, bar3 opens within bar2 body
        if not (o1 < o2 < c1 and o2 < o3 < c2):
            continue
        # each closes near its high (upper shadow ≤ 30% of body)
        if (_upper(o1, h1, c1) > 0.3 * _body(o1, c1) or
                _upper(o2, h2, c2) > 0.3 * _body(o2, c2) or
                _upper(o3, h3, c3) > 0.3 * _body(o3, c3)):
            continue
        # progressing upward
        if c1 < c2 < c3:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["three_black_crows"])
def three_black_crows(opens, highs, lows, closes):
    """3 consecutive long black candles, each opening within prior body, closing near low → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not (_is_black(o1, c1) and _is_black(o2, c2) and _is_black(o3, c3)):
            continue
        if _body(o1, c1) < 0.7 * a or _body(o2, c2) < 0.7 * a or _body(o3, c3) < 0.7 * a:
            continue
        # bar2 opens within bar1 body, bar3 opens within bar2 body
        if not (c1 < o2 < o1 and c2 < o3 < o2):
            continue
        # each closes near its low (lower shadow ≤ 30% of body)
        if (_lower(o1, l1, c1) > 0.3 * _body(o1, c1) or
                _lower(o2, l2, c2) > 0.3 * _body(o2, c2) or
                _lower(o3, l3, c3) > 0.3 * _body(o3, c3)):
            continue
        # progressing downward
        if c1 > c2 > c3:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["identical_three_crows"])
def identical_three_crows(opens, highs, lows, closes):
    """Three black crows where each bar opens ≈ at the prior close → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not (_is_black(o1, c1) and _is_black(o2, c2) and _is_black(o3, c3)):
            continue
        if _body(o1, c1) < 0.7 * a or _body(o2, c2) < 0.7 * a or _body(o3, c3) < 0.7 * a:
            continue
        # each bar opens at ≈ prior close (tolerance 5% of avg body)
        tol = 0.05 * a
        if abs(o2 - c1) > tol or abs(o3 - c2) > tol:
            continue
        if c1 > c2 > c3:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["three_inside"])
def three_inside(opens, highs, lows, closes):
    """Harami then confirming third bar.
    Bar1 black, bar2 white harami inside bar1, bar3 white confirming (close > bar1 open) → +100.
    Bar1 white, bar2 black harami inside bar1, bar3 black confirming (close < bar1 open) → -100.
    """
    n = len(closes)
    out = [0] * n
    for i in range(2, n):
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        b1_hi = max(o1, c1); b1_lo = min(o1, c1)
        b2_hi = max(o2, c2); b2_lo = min(o2, c2)
        # bar2 body must be inside bar1 body
        if b2_hi >= b1_hi or b2_lo <= b1_lo:
            continue
        if _is_black(o1, c1) and _is_white(o2, c2) and _is_white(o3, c3) and c3 > o1:
            out[i] = 100
        elif _is_white(o1, c1) and _is_black(o2, c2) and _is_black(o3, c3) and c3 < o1:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["three_outside"])
def three_outside(opens, highs, lows, closes):
    """Engulfing then confirming third bar.
    Bar1 black, bar2 white engulfs, bar3 white confirming (close > bar2 close) → +100.
    Bar1 white, bar2 black engulfs, bar3 black confirming (close < bar2 close) → -100.
    """
    n = len(closes)
    out = [0] * n
    for i in range(2, n):
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bullish outside: bar1 black, bar2 white engulfs bar1
        if _is_black(o1, c1) and _is_white(o2, c2) and c2 >= o1 and o2 <= c1:
            if _is_white(o3, c3) and c3 > c2:
                out[i] = 100
        # bearish outside: bar1 white, bar2 black engulfs bar1
        elif _is_white(o1, c1) and _is_black(o2, c2) and o2 >= c1 and c2 <= o1:
            if _is_black(o3, c3) and c3 < c2:
                out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["three_line_strike"])
def three_line_strike(opens, highs, lows, closes):
    """3 same-colour trend candles then a 4th that engulfs all three → ±100 (reversal signal).
    Three whites then a big black → +100 (bull reversal implied).
    Three blacks then a big white → -100 (bear reversal implied).
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(3, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o2, h2, l2, c2 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o3, h3, l3, c3 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o4, h4, l4, c4 = opens[i], highs[i], lows[i], closes[i]
        # three white soldiers → fourth black engulfs all three
        if (_is_white(o1, c1) and _is_white(o2, c2) and _is_white(o3, c3)
                and c1 < c2 < c3 and _is_black(o4, c4)
                and o4 >= c3 and c4 <= o1):
            out[i] = 100
        # three black crows → fourth white engulfs all three
        elif (_is_black(o1, c1) and _is_black(o2, c2) and _is_black(o3, c3)
              and c1 > c2 > c3 and _is_white(o4, c4)
              and o4 <= c3 and c4 >= o1):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["three_stars_in_south"])
def three_stars_in_south(opens, highs, lows, closes):
    """Three black candles of diminishing range in a downtrend → +100 (bullish reversal)."""
    n = len(closes)
    out = [0] * n
    for i in range(2, n):
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not (_is_black(o1, c1) and _is_black(o2, c2) and _is_black(o3, c3)):
            continue
        rng1 = _range(h1, l1); rng2 = _range(h2, l2); rng3 = _range(h3, l3)
        if rng1 <= 0 or rng2 <= 0 or rng3 <= 0:
            continue
        # diminishing range
        if not (rng1 > rng2 > rng3):
            continue
        # bar2 low higher than bar1 low (upward pivot on lows)
        if l2 <= l1:
            continue
        # bar3 (star): high < bar2 high, low > bar2 low (inside bar2 range)
        if h3 >= h2 or l3 <= l2:
            continue
        out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["abandoned_baby"])
def abandoned_baby(opens, highs, lows, closes):
    """Doji island reversal: bar1 long, bar2 is a doji with gaps on BOTH sides, bar3 confirming.
    Bullish: long black → gap-down doji → gap-up white → +100.
    Bearish: long white → gap-up doji → gap-down black → -100.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bar2 must be a doji
        if _body(o2, c2) > 0.1 * a or _range(h2, l2) <= 0:
            continue
        # bullish: bar1 long black, doji gaps down (h2 < l1), bar3 white gaps up (l3 >= h2)
        if (_is_black(o1, c1) and _body(o1, c1) >= a
                and h2 < l1 and _is_white(o3, c3) and l3 >= h2):
            out[i] = 100
        # bearish: bar1 long white, doji gaps up (l2 > h1), bar3 black gaps down (h3 <= l2)
        elif (_is_white(o1, c1) and _body(o1, c1) >= a
              and l2 > h1 and _is_black(o3, c3) and h3 <= l2):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["advance_block"])
def advance_block(opens, highs, lows, closes):
    """Three white candles with weakening bodies and/or growing upper shadows → -100 (bearish warning)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not (_is_white(o1, c1) and _is_white(o2, c2) and _is_white(o3, c3)):
            continue
        b1 = _body(o1, c1); b2 = _body(o2, c2); b3 = _body(o3, c3)
        if b1 <= 0 or b2 <= 0 or b3 <= 0:
            continue
        u1 = _upper(o1, h1, c1); u2 = _upper(o2, h2, c2); u3 = _upper(o3, h3, c3)
        # progressing upward
        if not (c1 < c2 < c3):
            continue
        # weakening: bodies shrinking OR upper shadows growing
        weakening_bodies = b2 < b1 and b3 < b2
        growing_shadows = u2 > u1 and u3 > u2
        if weakening_bodies or growing_shadows:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["stalled_pattern"])
def stalled_pattern(opens, highs, lows, closes):
    """Two long whites then a small white body near the top (stalling) → -100 (bearish warning)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not (_is_white(o1, c1) and _is_white(o2, c2) and _is_white(o3, c3)):
            continue
        # bars 1 and 2 must be long; bar3 must be small (body < 50% avg)
        if _body(o1, c1) < a or _body(o2, c2) < a:
            continue
        if _body(o3, c3) >= 0.5 * a:
            continue
        # progressing upward on bars 1-2, bar3 opens near bar2 close (near top)
        if c1 < c2 and o3 >= c2 * 0.98:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["two_crows"])
def two_crows(opens, highs, lows, closes):
    """Long white, gap-up black (bar2), then black bar3 closing into bar1 body → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bar1: long white
        if not _is_white(o1, c1) or _body(o1, c1) < a:
            continue
        # bar2: black, gaps up (opens above bar1 close)
        if not _is_black(o2, c2) or o2 <= c1:
            continue
        # bar3: black, closes inside bar1 body (between o1 and c1)
        if not _is_black(o3, c3):
            continue
        if o1 < c3 < c1:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["upside_gap_two_crows"])
def upside_gap_two_crows(opens, highs, lows, closes):
    """Upside gap two crows: bar1 long white, bar2 black gaps up, bar3 black engulfs bar2 but stays above bar1 close → -100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bar1: long white
        if not _is_white(o1, c1) or _body(o1, c1) < a:
            continue
        # bar2: black, gaps up (opens above bar1 close)
        if not _is_black(o2, c2) or o2 <= c1:
            continue
        # bar3: black, engulfs bar2 (opens above bar2 open, closes below bar2 close)
        # but closes above bar1 close (stays in the gap)
        if not _is_black(o3, c3):
            continue
        if o3 >= o2 and c3 <= c2 and c3 > c1:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["tristar"])
def tristar(opens, highs, lows, closes):
    """Three dojis: middle gaps away from first, third gaps back → ±100 (reversal by gap direction).
    Bullish (+100): middle doji gaps DOWN, third doji gaps back UP.
    Bearish (-100): middle doji gaps UP, third doji gaps back DOWN.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # all three must be dojis
        if (_body(o1, c1) > 0.1 * a or _range(h1, l1) <= 0
                or _body(o2, c2) > 0.1 * a or _range(h2, l2) <= 0
                or _body(o3, c3) > 0.1 * a or _range(h3, l3) <= 0):
            continue
        # Use body midpoints to determine gap direction (dojis may have overlapping shadows)
        mid1 = (max(o1, c1) + min(o1, c1)) / 2
        mid2 = (max(o2, c2) + min(o2, c2)) / 2
        mid3 = (max(o3, c3) + min(o3, c3)) / 2
        # bullish: middle doji is lower than first, third is higher than middle
        if mid2 < mid1 and mid3 > mid2:
            out[i] = 100
        # bearish: middle doji is higher than first, third is lower than middle
        elif mid2 > mid1 and mid3 < mid2:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["unique_three_river"])
def unique_three_river(opens, highs, lows, closes):
    """Long black, harami-like black with lower low, small white → +100 (bullish reversal)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bar1: long black
        if not _is_black(o1, c1) or _body(o1, c1) < a:
            continue
        # bar2: black, body inside bar1, lower low (hammer-like)
        if not _is_black(o2, c2):
            continue
        if not (min(o2, c2) > min(o1, c1) and max(o2, c2) < max(o1, c1)):
            continue
        if l2 >= l1:
            continue
        # bar3: small white body, closes below bar2 close (moderate)
        if not _is_white(o3, c3):
            continue
        if _body(o3, c3) >= _body(o2, c2):
            continue
        out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["stick_sandwich"])
def stick_sandwich(opens, highs, lows, closes):
    """Black, white, black — two blacks with equal closes → +100 (bullish reversal at support)."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(2, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        if not (_is_black(o1, c1) and _is_white(o2, c2) and _is_black(o3, c3)):
            continue
        # two blacks with equal closes (tolerance 3% of avg body)
        if abs(c3 - c1) <= 0.03 * a + 1e-9:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["ladder_bottom"])
def ladder_bottom(opens, highs, lows, closes):
    """Four black candles stepping lower, fourth with upper shadow, then white reversal → +100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(4, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 4], highs[i - 4], lows[i - 4], closes[i - 4]
        o2, h2, l2, c2 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o3, h3, l3, c3 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o4, h4, l4, c4 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o5, h5, l5, c5 = opens[i], highs[i], lows[i], closes[i]
        # bars 1-4: all black, progressively lower closes
        if not (_is_black(o1, c1) and _is_black(o2, c2)
                and _is_black(o3, c3) and _is_black(o4, c4)):
            continue
        if not (c1 > c2 > c3 > c4):
            continue
        # bar4 has an upper shadow (some upper wick)
        if _upper(o4, h4, c4) <= 0:
            continue
        # bar5: white reversal, closes above bar4 open
        if _is_white(o5, c5) and c5 > o4:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["concealing_baby_swallow"])
def concealing_baby_swallow(opens, highs, lows, closes):
    """Four black candles: first two marubozu, third gaps down with upper shadow, fourth engulfs third → +100."""
    n = len(closes)
    out = [0] * n
    for i in range(3, n):
        o1, h1, l1, c1 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o2, h2, l2, c2 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o3, h3, l3, c3 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o4, h4, l4, c4 = opens[i], highs[i], lows[i], closes[i]
        # all four must be black
        if not (_is_black(o1, c1) and _is_black(o2, c2)
                and _is_black(o3, c3) and _is_black(o4, c4)):
            continue
        # bars 1 and 2 must be marubozu (no shadows)
        if not (_is_marubozu(o1, h1, l1, c1) and _is_marubozu(o2, h2, l2, c2)):
            continue
        # bar3 has an upper shadow (high > open for black = upper shadow exists)
        if _upper(o3, h3, c3) <= 0:
            continue
        # bar4 engulfs bar3 (bar4 range covers bar3 range)
        if h4 >= h3 and l4 <= l3:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["rise_fall_three_methods"])
def rise_fall_three_methods(opens, highs, lows, closes):
    """5-bar pattern: long candle, 3 small opposite-colour candles within its range, long candle continuing.
    Rising three methods → +100 (long white, 3 small blacks, long white closing higher).
    Falling three methods → -100 (long black, 3 small whites, long black closing lower).
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(4, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 4], highs[i - 4], lows[i - 4], closes[i - 4]
        o2, h2, l2, c2 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o3, h3, l3, c3 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o4, h4, l4, c4 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o5, h5, l5, c5 = opens[i], highs[i], lows[i], closes[i]
        b1 = _body(o1, c1)
        if b1 < a:
            continue
        # middle three bars must be small (body < 50% of avg)
        if (_body(o2, c2) >= 0.5 * a or _body(o3, c3) >= 0.5 * a
                or _body(o4, c4) >= 0.5 * a):
            continue
        # rising three methods
        if (_is_white(o1, c1) and _is_black(o2, c2) and _is_black(o3, c3) and _is_black(o4, c4)
                and _is_white(o5, c5) and _body(o5, c5) >= a
                and l2 > l1 and h2 < h1  # middle bars within bar1 range
                and l3 > l1 and h3 < h1
                and l4 > l1 and h4 < h1
                and c5 > c1):
            out[i] = 100
        # falling three methods
        elif (_is_black(o1, c1) and _is_white(o2, c2) and _is_white(o3, c3) and _is_white(o4, c4)
              and _is_black(o5, c5) and _body(o5, c5) >= a
              and h2 < h1 and l2 > l1
              and h3 < h1 and l3 > l1
              and h4 < h1 and l4 > l1
              and c5 < c1):
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["mat_hold"])
def mat_hold(opens, highs, lows, closes):
    """Bullish mat hold (gap variant of rising three methods): long white, gap-up small blacks, long white → +100."""
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(4, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 4], highs[i - 4], lows[i - 4], closes[i - 4]
        o2, h2, l2, c2 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o3, h3, l3, c3 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o4, h4, l4, c4 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o5, h5, l5, c5 = opens[i], highs[i], lows[i], closes[i]
        # bar1: long white
        if not _is_white(o1, c1) or _body(o1, c1) < a:
            continue
        # bar2: small black, opens above bar1 close (gap up)
        if not _is_black(o2, c2) or _body(o2, c2) >= 0.5 * a or o2 <= c1:
            continue
        # bars 3 and 4: small candles staying above bar1 open
        if _body(o3, c3) >= 0.5 * a or _body(o4, c4) >= 0.5 * a:
            continue
        if l3 <= o1 or l4 <= o1:
            continue
        # bar5: long white, close above bar1 close
        if _is_white(o5, c5) and _body(o5, c5) >= a and c5 > c1:
            out[i] = 100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["hikkake"])
def hikkake(opens, highs, lows, closes):
    """Inside bar then false breakout that reverses: 4-bar pattern.
    Bar1: reference; bar2: inside bar (h<b1.h, l>b1.l); bar3: false breakout;
    bar4: reversal back through bar2's opposite side → ±100.
    Bullish (+100): bar3 breaks below bar2 low, bar4 closes above bar2 high.
    Bearish (-100): bar3 breaks above bar2 high, bar4 closes below bar2 low.
    """
    n = len(closes)
    out = [0] * n
    for i in range(3, n):
        o1, h1, l1, c1 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o2, h2, l2, c2 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o3, h3, l3, c3 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o4, h4, l4, c4 = opens[i], highs[i], lows[i], closes[i]
        # bar2 must be an inside bar relative to bar1
        if h2 >= h1 or l2 <= l1:
            continue
        # bullish hikkake: bar3 breaks below bar2 low, bar4 reverses above bar2 high
        if l3 < l2 and c4 > h2:
            out[i] = 100
        # bearish hikkake: bar3 breaks above bar2 high, bar4 reverses below bar2 low
        elif h3 > h2 and c4 < l2:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["hikkake_mod"])
def hikkake_mod(opens, highs, lows, closes):
    """Modified hikkake: 5-bar — inside bar, false breakout, reversal, confirming bar → ±100."""
    n = len(closes)
    out = [0] * n
    for i in range(4, n):
        o1, h1, l1, c1 = opens[i - 4], highs[i - 4], lows[i - 4], closes[i - 4]
        o2, h2, l2, c2 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o3, h3, l3, c3 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o4, h4, l4, c4 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o5, h5, l5, c5 = opens[i], highs[i], lows[i], closes[i]
        # bar2 is an inside bar
        if h2 >= h1 or l2 <= l1:
            continue
        # bullish: bar3 false bear break, bar4 reverses above bar2 high, bar5 confirms up
        if l3 < l2 and c4 > h2 and c5 > c4:
            out[i] = 100
        # bearish: bar3 false bull break, bar4 reverses below bar2 low, bar5 confirms down
        elif h3 > h2 and c4 < l2 and c5 < c4:
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["xside_gap_three_methods"])
def xside_gap_three_methods(opens, highs, lows, closes):
    """Gap then a filling candle in a trend — continuation pattern.
    Upside gap three methods (+100): two whites with gap, third black fills partway but stays above bar1 close.
    Downside gap three methods (-100): two blacks with gap, third white fills partway but stays below bar1 close.
    """
    n = len(closes)
    out = [0] * n
    for i in range(2, n):
        o1, h1, l1, c1 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o2, h2, l2, c2 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o3, h3, l3, c3 = opens[i], highs[i], lows[i], closes[i]
        # bullish (upside gap): bars 1 and 2 white, gap up between them, bar3 black partial fill
        if (_is_white(o1, c1) and _is_white(o2, c2)
                and o2 > c1  # gap up
                and _is_black(o3, c3)
                and c3 > c1):  # stays above bar1 close (in the gap)
            out[i] = 100
        # bearish (downside gap): bars 1 and 2 black, gap down, bar3 white partial fill
        elif (_is_black(o1, c1) and _is_black(o2, c2)
              and o2 < c1  # gap down
              and _is_white(o3, c3)
              and c3 < c1):  # stays below bar1 close
            out[i] = -100
    return out


@indicator(category="pattern", inputs=["open", "high", "low", "close"], outputs=["breakaway"])
def breakaway(opens, highs, lows, closes):
    """5-bar breakaway: gap, 3-bar run, then reversal closing into the gap → ±100.
    Bullish (+100): bar1 long black, bars 2-4 black trending down, bar5 white reversal closes above bar2 open.
    Bearish (-100): bar1 long white, bars 2-4 white trending up, bar5 black reversal closes below bar2 open.
    """
    n = len(closes)
    avg = _avg_body(opens, closes)
    out = [0] * n
    for i in range(4, n):
        a = avg[i]
        if a is None or a <= 0:
            continue
        o1, h1, l1, c1 = opens[i - 4], highs[i - 4], lows[i - 4], closes[i - 4]
        o2, h2, l2, c2 = opens[i - 3], highs[i - 3], lows[i - 3], closes[i - 3]
        o3, h3, l3, c3 = opens[i - 2], highs[i - 2], lows[i - 2], closes[i - 2]
        o4, h4, l4, c4 = opens[i - 1], highs[i - 1], lows[i - 1], closes[i - 1]
        o5, h5, l5, c5 = opens[i], highs[i], lows[i], closes[i]
        # bullish breakaway: 4 black bars declining, white reversal
        if (_is_black(o1, c1) and _body(o1, c1) >= 0.7 * a
                and _is_black(o2, c2) and _is_black(o3, c3) and _is_black(o4, c4)
                and c1 > c2 and c2 > c3 and c3 > c4
                and _is_white(o5, c5) and c5 >= o2):
            out[i] = 100
        # bearish breakaway: 4 white bars rising, black reversal
        elif (_is_white(o1, c1) and _body(o1, c1) >= 0.7 * a
              and _is_white(o2, c2) and _is_white(o3, c3) and _is_white(o4, c4)
              and c1 < c2 and c2 < c3 and c3 < c4
              and _is_black(o5, c5) and c5 <= o2):
            out[i] = -100
    return out
