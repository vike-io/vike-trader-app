import math

from .base import Param, indicator, smooth_defined
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


# ---------------------------------------------------------------------------
# Tier B volatility — Task 3
# ---------------------------------------------------------------------------


@indicator(
    category="volatility",
    inputs=["close"],
    params=[Param("period", "int", 14, 2, 200, 1)],
    outputs=["rvi"],
)
def relative_volatility(values, period: int = 14):
    """Relative Volatility Index — RSI-like on rolling stddev.

    For each bar compute ``sd = stddev(close, period)``.  Then:
      ``u[i] = sd[i]`` if ``close[i] > close[i-1]`` else ``0``
      ``d[i] = sd[i]`` if ``close[i] < close[i-1]`` else ``0``
    ``rvi = 100 * EMA(u, period) / (EMA(u, period) + EMA(d, period))``.

    Uses Wilder smoothing via the standard EMA helper.
    Output name: ``rvi`` (distinct from momentum ``relative_vigor``).
    """
    n = len(values)

    # Step 1: rolling stddev series
    sd_series = stddev(values, period)

    # Step 2: split into up-stddev and down-stddev from index 1 onward
    # (bar 0 has no previous close so both u and d default to 0)
    u_raw: list[float | None] = [None] * n
    d_raw: list[float | None] = [None] * n
    for i in range(1, n):
        sd = sd_series[i]
        if sd is None:
            continue
        if values[i] > values[i - 1]:
            u_raw[i] = sd
            d_raw[i] = 0.0
        elif values[i] < values[i - 1]:
            u_raw[i] = 0.0
            d_raw[i] = sd
        else:
            u_raw[i] = 0.0
            d_raw[i] = 0.0

    # Step 3: EMA of u and d over the defined tail
    defined_u = [(i, v) for i, v in enumerate(u_raw) if v is not None]
    defined_d = [(i, v) for i, v in enumerate(d_raw) if v is not None]

    ema_u: list[float | None] = [None] * n
    ema_d: list[float | None] = [None] * n

    if len(defined_u) >= period:
        eu = ema([v for _, v in defined_u], period)
        ed = ema([v for _, v in defined_d], period)
        for (idx, _), eu_v, ed_v in zip(defined_u, eu, ed, strict=True):
            ema_u[idx] = eu_v
            ema_d[idx] = ed_v

    # Step 4: RVI formula
    out: list[float | None] = [None] * n
    for i in range(n):
        eu_v, ed_v = ema_u[i], ema_d[i]
        if eu_v is not None and ed_v is not None:
            denom = eu_v + ed_v
            out[i] = 100.0 * eu_v / denom if denom != 0.0 else 50.0
    return out


@indicator(
    category="volatility",
    inputs=["high", "low"],
    params=[Param("period", "int", 252, 2, 1000, 1)],
    outputs=["high_n", "low_n"],
)
def high_low_52w(highs, lows, period: int = 252):
    """Rolling N-period high and low.

    ``high_n[i] = max(high[i-period+1..i])``
    ``low_n[i]  = min(low[i-period+1..i])``

    None during warm-up (first ``period - 1`` bars).
    Returns ``(high_n, low_n)``.
    """
    n = len(highs)
    high_n: list[float | None] = [None] * n
    low_n: list[float | None] = [None] * n
    for i in range(period - 1, n):
        high_n[i] = max(highs[i - period + 1 : i + 1])
        low_n[i] = min(lows[i - period + 1 : i + 1])
    return high_n, low_n


@indicator(category="volatility", inputs=["high", "low"], params=[Param("period", "int", 25, 2, 200, 1), Param("ema_period", "int", 9, 2, 50, 1)], outputs=["mass"])
def mass(highs, lows, period: int = 25, ema_period: int = 9):
    """Mass Index: ``sum(EMA(H-L, ema) / EMA(EMA(H-L, ema), ema), period)``."""
    n = len(highs)
    hl = [highs[i] - lows[i] for i in range(n)]
    ema1 = ema(hl, ema_period)
    # EMA of ema1 (defined portion only, mapped back)
    ema2 = smooth_defined(ema1, ema, ema_period)
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
