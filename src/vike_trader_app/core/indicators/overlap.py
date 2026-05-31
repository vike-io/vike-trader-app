import math

from .base import Param, indicator


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["sma"])
def sma(values, period: int):
    """Simple moving average over ``period``."""
    out: list[float | None] = []
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        out.append(run / period if i >= period - 1 else None)
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["ema"])
def ema(values, period: int):
    """Exponential moving average, seeded with the first full SMA."""
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    mult = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * mult + prev * (1 - mult)
        out[i] = prev
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["wma"])
def wma(values, period: int):
    """Weighted moving average (linear weights, recent heaviest)."""
    n = len(values)
    out: list[float | None] = [None] * n
    denom = period * (period + 1) / 2.0
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        out[i] = sum((k + 1) * window[k] for k in range(period)) / denom
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["dema"])
def dema(values, period: int = 20):
    """Double EMA: ``2*EMA(p) - EMA(EMA(p))``, reducing lag versus a plain EMA."""
    e1 = ema(values, period)
    # compute EMA of the defined e1 tail, mapped back to aligned positions
    defined = [(i, v) for i, v in enumerate(e1) if v is not None]
    e2: list[float | None] = [None] * len(values)
    if len(defined) >= period:
        e2_vals = ema([v for _, v in defined], period)
        for (i, _), ev in zip(defined, e2_vals, strict=True):
            e2[i] = ev
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if e1[i] is not None and e2[i] is not None:
            out[i] = 2.0 * e1[i] - e2[i]
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["tema"])
def tema(values, period: int = 20):
    """Triple EMA: ``3*EMA - 3*EMA(EMA) + EMA(EMA(EMA))``, minimal lag."""
    e1 = ema(values, period)
    defined1 = [(i, v) for i, v in enumerate(e1) if v is not None]
    e2: list[float | None] = [None] * len(values)
    if len(defined1) >= period:
        e2_vals = ema([v for _, v in defined1], period)
        for (i, _), ev in zip(defined1, e2_vals, strict=True):
            e2[i] = ev
    defined2 = [(i, v) for i, v in enumerate(e2) if v is not None]
    e3: list[float | None] = [None] * len(values)
    if len(defined2) >= period:
        e3_vals = ema([v for _, v in defined2], period)
        for (i, _), ev in zip(defined2, e3_vals, strict=True):
            e3[i] = ev
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if e1[i] is not None and e2[i] is not None and e3[i] is not None:
            out[i] = 3.0 * e1[i] - 3.0 * e2[i] + e3[i]
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["trima"])
def trima(values, period: int = 20):
    """Triangular MA = SMA of SMA: ``SMA(SMA(values, ceil(p/2)), floor(p/2)+1)``."""
    p1 = math.ceil(period / 2)
    p2 = math.floor(period / 2) + 1
    inner = sma(values, p1)
    # pass only the defined portion to the outer SMA, mapped back
    defined = [(i, v) for i, v in enumerate(inner) if v is not None]
    out: list[float | None] = [None] * len(values)
    if len(defined) >= p2:
        outer_vals = sma([v for _, v in defined], p2)
        for (i, _), ov in zip(defined, outer_vals, strict=True):
            out[i] = ov
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 14, 2, 400, 1)], outputs=["smma"])
def smma(values, period: int = 14):
    """Wilder/RMA smoothed MA: recursive ``smma[i] = (smma[i-1]*(p-1) + v[i]) / p``, seeded with SMA(p)."""
    n = len(values)
    out: list[float | None] = [None] * n
    if n < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["zlema"])
def zlema(values, period: int = 20):
    """Zero-lag EMA: EMA of de-lagged series ``values + (values - values[lag])``, lag=(p-1)//2."""
    n = len(values)
    lag = (period - 1) // 2
    # build the de-lagged series (valid from index ``lag`` onward)
    delagged: list[float] = [0.0] * n
    for i in range(n):
        if i >= lag:
            delagged[i] = values[i] + (values[i] - values[i - lag])
        else:
            delagged[i] = values[i]
    return ema(delagged, period)


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["hma"])
def hma(values, period: int = 20):
    """Hull MA: ``WMA(2*WMA(p//2) - WMA(p), int(sqrt(p)))`` — responds faster than EMA."""
    half = max(2, period // 2)
    sqrt_p = max(2, int(math.sqrt(period)))
    n = len(values)
    w_half = wma(values, half)
    w_full = wma(values, period)
    # combined series: 2*WMA(p/2) - WMA(p), None where either is None
    combined: list[float] = [0.0] * n
    for i in range(n):
        wh = w_half[i]
        wf = w_full[i]
        combined[i] = (2.0 * wh - wf) if (wh is not None and wf is not None) else float("nan")
    # replace NaN slots with 0 for the inner WMA (they will remain None in output)
    safe = [v if not math.isnan(v) else 0.0 for v in combined]
    raw = wma(safe, sqrt_p)
    # mask any position where the combined series was still NaN inside the sqrt_p window
    out: list[float | None] = [None] * n
    for i in range(sqrt_p - 1, n):
        window_combined = combined[i - sqrt_p + 1 : i + 1]
        if all(not math.isnan(v) for v in window_combined):
            out[i] = raw[i]
    return out


@indicator(category="overlap", inputs=["close", "volume"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["vwma"])
def vwma(closes, volumes, period: int = 20):
    """Volume-weighted MA: rolling ``sum(close*vol, p) / sum(vol, p)``."""
    n = len(closes)
    out: list[float | None] = [None] * n
    run_pv = 0.0
    run_v = 0.0
    for i in range(n):
        run_pv += closes[i] * volumes[i]
        run_v += volumes[i]
        if i >= period:
            run_pv -= closes[i - period] * volumes[i - period]
            run_v -= volumes[i - period]
        if i >= period - 1:
            out[i] = run_pv / run_v if run_v != 0 else None
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1), Param("v", "float", 0.7, 0.0, 1.0, 0.05)], outputs=["t3"])
def t3(values, period: int = 20, v: float = 0.7):
    """Tillson T3: GD(GD(GD(x))) where GD(x) = EMA(x)*(1+v) - EMA(EMA(x))*v."""
    def _gd(series):
        """One GD pass: (1+v)*EMA - v*EMA(EMA)."""
        defined = [(i, val) for i, val in enumerate(series) if val is not None]
        e1_inner: list[float | None] = [None] * len(series)
        if len(defined) >= period:
            e1_vals = ema([val for _, val in defined], period)
            for (i, _), ev in zip(defined, e1_vals, strict=True):
                e1_inner[i] = ev
        defined2 = [(i, val) for i, val in enumerate(e1_inner) if val is not None]
        e2_inner: list[float | None] = [None] * len(series)
        if len(defined2) >= period:
            e2_vals = ema([val for _, val in defined2], period)
            for (i, _), ev in zip(defined2, e2_vals, strict=True):
                e2_inner[i] = ev
        result: list[float | None] = [None] * len(series)
        for i in range(len(series)):
            if e1_inner[i] is not None and e2_inner[i] is not None:
                result[i] = (1.0 + v) * e1_inner[i] - v * e2_inner[i]
        return result

    gd1 = _gd(values)
    gd2 = _gd(gd1)
    gd3 = _gd(gd2)
    return gd3


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1), Param("offset", "float", 0.85, 0.0, 1.0, 0.05), Param("sigma", "float", 6.0, 1.0, 20.0, 0.5)], outputs=["alma"])
def alma(values, period: int = 20, offset: float = 0.85, sigma: float = 6.0):
    """Arnaud Legoux MA: Gaussian-weighted rolling window with adjustable offset and sigma."""
    n = len(values)
    out: list[float | None] = [None] * n
    m = offset * (period - 1)
    s = period / sigma
    # precompute normalised weights for the window
    raw_weights = [math.exp(-((k - m) ** 2) / (2.0 * s * s)) for k in range(period)]
    weight_sum = sum(raw_weights)
    weights = [w / weight_sum for w in raw_weights]
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        out[i] = sum(weights[k] * window[k] for k in range(period))
    return out


@indicator(category="overlap", inputs=["close"], params=[Param("period", "int", 14, 2, 400, 1)], outputs=["midpoint"])
def midpoint(values, period: int = 14):
    """Midpoint: ``(max(values, p) + min(values, p)) / 2`` over a rolling window."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        out[i] = (max(window) + min(window)) / 2.0
    return out


@indicator(category="overlap", inputs=["high", "low"], params=[Param("period", "int", 14, 2, 400, 1)], outputs=["midprice"])
def midprice(highs, lows, period: int = 14):
    """Midprice: ``(max(high, p) + min(low, p)) / 2`` over a rolling window."""
    n = len(highs)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        out[i] = (max(highs[i - period + 1 : i + 1]) + min(lows[i - period + 1 : i + 1])) / 2.0
    return out
