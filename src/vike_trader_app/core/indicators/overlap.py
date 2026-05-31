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
