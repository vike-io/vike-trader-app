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


# ── Task 4: additional volatility indicators ──────────────────────────────────


@indicator(category="volatility", inputs=["high", "low", "close"], params=[Param("period", "int", 14, 2, 100, 1)], outputs=["natr"])
def natr(highs, lows, closes, period: int = 14):
    """Normalized ATR: ``100 * ATR(period) / close``, aligned to input."""
    atr_vals = atr(highs, lows, closes, period)
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(n):
        if atr_vals[i] is not None and closes[i] != 0.0:
            out[i] = 100.0 * atr_vals[i] / closes[i]
    return out


@indicator(category="volatility", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["stddev"])
def stddev(values, period: int = 20):
    """Rolling population standard deviation over ``period``, aligned to input."""
    n = len(values)
    out: list[float | None] = [None] * n
    run_sum = 0.0
    run_sum2 = 0.0
    for i in range(n):
        run_sum += values[i]
        run_sum2 += values[i] ** 2
        if i >= period:
            run_sum -= values[i - period]
            run_sum2 -= values[i - period] ** 2
        if i >= period - 1:
            mean = run_sum / period
            var = run_sum2 / period - mean * mean
            out[i] = math.sqrt(max(var, 0.0))
    return out


@indicator(category="volatility", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1), Param("ann", "int", 365, 1, 365, 1)], outputs=["hvol"])
def hvol(values, period: int = 20, ann: int = 365):
    """Historical volatility: ``stddev(log returns, period) * sqrt(ann) * 100``."""
    n = len(values)
    # compute log returns (aligned, first element is None)
    log_rets: list[float | None] = [None] * n
    for i in range(1, n):
        if values[i] > 0 and values[i - 1] > 0:
            log_rets[i] = math.log(values[i] / values[i - 1])
    out: list[float | None] = [None] * n
    run_sum = 0.0
    run_sum2 = 0.0
    count = 0
    # use a deque-like approach over the log returns
    # We need a window of `period` log returns; log_rets[0] is None so window starts from index 1
    buf: list[float] = []
    for i in range(n):
        if log_rets[i] is not None:
            buf.append(log_rets[i])
            run_sum += log_rets[i]
            run_sum2 += log_rets[i] ** 2
            if len(buf) > period:
                old = buf.pop(0)
                run_sum -= old
                run_sum2 -= old ** 2
            if len(buf) == period:
                mean = run_sum / period
                var = run_sum2 / period - mean * mean
                out[i] = math.sqrt(max(var, 0.0)) * math.sqrt(ann) * 100.0
    return out


@indicator(category="volatility", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1), Param("k", "float", 2.0, 0.5, 5.0, 0.1)], outputs=["pctb"])
def bbands_pctb(values, period: int = 20, k: float = 2.0):
    """Bollinger %B: ``(close - lower) / (upper - lower)`` using Bollinger bands."""
    upper, mid, lower = bollinger(values, period, k)
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(n):
        if upper[i] is not None:
            bw = upper[i] - lower[i]
            if bw != 0.0:
                out[i] = (values[i] - lower[i]) / bw
            # if bw == 0 (flat series), leave as None
    return out


@indicator(category="volatility", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1), Param("k", "float", 2.0, 0.5, 5.0, 0.1)], outputs=["width"])
def bbands_width(values, period: int = 20, k: float = 2.0):
    """Bollinger Band Width: ``(upper - lower) / mid`` using Bollinger bands."""
    upper, mid, lower = bollinger(values, period, k)
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(n):
        if mid[i] is not None and mid[i] != 0.0:
            out[i] = (upper[i] - lower[i]) / mid[i]
    return out


@indicator(category="volatility", inputs=["high", "low"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["width"])
def donchian_width(highs, lows, period: int = 20):
    """Donchian channel width: ``upper - lower`` using Donchian channel."""
    upper, mid, lower = donchian(highs, lows, period)
    n = len(highs)
    out: list[float | None] = [None] * n
    for i in range(n):
        if upper[i] is not None:
            out[i] = upper[i] - lower[i]
    return out


@indicator(category="volatility", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["ulcer"])
def ulcer(values, period: int = 14):
    """Ulcer Index: ``sqrt(mean(drawdown_pct^2, period))`` where ``drawdown_pct = 100*(close - max(close,p))/max(close,p)``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        peak = max(window)
        sq_sum = sum(((c - peak) / peak * 100.0) ** 2 for c in window) if peak != 0.0 else 0.0
        out[i] = math.sqrt(sq_sum / period)
    return out


@indicator(category="volatility", inputs=["high", "low", "close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["chop"])
def chop(highs, lows, closes, period: int = 14):
    """Choppiness Index: ``100 * log10(sum(TR,p) / (max(high,p) - min(low,p))) / log10(p)``."""
    trs = true_range(highs, lows, closes)
    n = len(closes)
    out: list[float | None] = [None] * n
    log_p = math.log10(period)
    for i in range(period - 1, n):
        window_tr = trs[i - period + 1 : i + 1]
        sum_tr = sum(window_tr)
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        rng = hh - ll
        if rng > 0 and sum_tr > 0:
            out[i] = 100.0 * math.log10(sum_tr / rng) / log_p
    return out


@indicator(category="volatility", inputs=["high", "low"], params=[Param("period", "int", 25, 2, 200, 1), Param("ema_period", "int", 9, 2, 50, 1)], outputs=["mass"])
def mass(highs, lows, period: int = 25, ema_period: int = 9):
    """Mass Index: ``sum(EMA(H-L, ema) / EMA(EMA(H-L, ema), ema), period)``."""
    n = len(highs)
    hl = [highs[i] - lows[i] for i in range(n)]
    ema1 = ema(hl, ema_period)
    # compute EMA of ema1 (defined portion only, mapped back)
    defined = [(i, v) for i, v in enumerate(ema1) if v is not None]
    ema2: list[float | None] = [None] * n
    if len(defined) >= ema_period:
        ema2_vals = ema([v for _, v in defined], ema_period)
        for (i, _), ev in zip(defined, ema2_vals, strict=True):
            ema2[i] = ev
    # ratio series
    ratio: list[float | None] = [None] * n
    for i in range(n):
        if ema1[i] is not None and ema2[i] is not None and ema2[i] != 0.0:
            ratio[i] = ema1[i] / ema2[i]
    # rolling sum of ratio over period
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = ratio[i - period + 1 : i + 1]
        if all(v is not None for v in window):
            out[i] = sum(window)
    return out
