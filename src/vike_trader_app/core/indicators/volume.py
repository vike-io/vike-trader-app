from .base import indicator


@indicator(category="volume", inputs=["close", "volume"], params=[], outputs=["obv"])
def obv(closes, volumes):
    """On-balance volume (cumulative signed volume), aligned. ``obv[0] = 0``."""
    n = len(closes)
    out = [0.0] * n
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


@indicator(category="volume", inputs=["high", "low", "close", "volume"], params=[], outputs=["vwap"])
def vwap(highs, lows, closes, volumes):
    """Cumulative (session) VWAP aligned to the input."""
    n = len(closes)
    out: list[float | None] = [None] * n
    cum_pv = 0.0
    cum_v = 0.0
    for i in range(n):
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        cum_pv += typical * volumes[i]
        cum_v += volumes[i]
        out[i] = (cum_pv / cum_v) if cum_v else None
    return out
