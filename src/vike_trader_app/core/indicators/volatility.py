import math

from .base import Param, indicator
from .overlap import ema, sma


@indicator(category="volatility", inputs=["high", "low", "close"], params=[Param("period", "int", 14, 2, 100, 1)], outputs=["atr"])
def atr(highs, lows, closes, period: int = 14):
    """Average True Range (Wilder), aligned to the input (``None`` warm-up)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    if n <= period:
        return out
    trs = [highs[0] - lows[0]]
    for i in range(1, n):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    prev = sum(trs[1 : period + 1]) / period
    out[period] = prev
    for i in range(period + 1, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


@indicator(category="volatility", inputs=["high", "low", "close"], params=[], outputs=["true_range"])
def true_range(highs, lows, closes):
    """True range, aligned (``TR[0] = high - low``; later bars are gap-aware)."""
    n = len(closes)
    out = [highs[0] - lows[0]] + [0.0] * (n - 1)
    for i in range(1, n):
        out[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return out


@indicator(category="volatility", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1), Param("k", "float", 2.0, 0.5, 5.0, 0.1)], outputs=["upper", "mid", "lower"])
def bollinger(values, period: int = 20, k: float = 2.0):
    """Bollinger Bands: returns ``(upper, mid, lower)`` aligned to ``values``."""
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1 : i + 1]
        m = mid[i]
        var = sum((x - m) ** 2 for x in window) / period
        sd = math.sqrt(var)
        upper[i] = m + k * sd
        lower[i] = m - k * sd
    return upper, mid, lower


@indicator(category="volatility", inputs=["high", "low", "close"], params=[Param("period", "int", 20, 2, 200, 1), Param("mult", "float", 2.0, 0.5, 5.0, 0.1)], outputs=["upper", "mid", "lower"])
def keltner(highs, lows, closes, period: int = 20, mult: float = 2.0):
    """Keltner channel: EMA(close) mid +/- ``mult`` * ATR. Returns ``(upper, mid, lower)``."""
    mid = ema(closes, period)
    rng = atr(highs, lows, closes, period)
    n = len(closes)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(n):
        if mid[i] is not None and rng[i] is not None:
            upper[i] = mid[i] + mult * rng[i]
            lower[i] = mid[i] - mult * rng[i]
    return upper, mid, lower


@indicator(category="volatility", inputs=["high", "low"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["upper", "mid", "lower"])
def donchian(highs, lows, period: int = 20):
    """Donchian channel: returns ``(upper, mid, lower)`` aligned to the input."""
    n = len(highs)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    mid: list[float | None] = [None] * n
    for i in range(period - 1, n):
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        upper[i], lower[i], mid[i] = hh, ll, (hh + ll) / 2.0
    return upper, mid, lower
