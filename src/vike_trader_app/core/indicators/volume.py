from .base import Param, indicator
from .overlap import ema, sma


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


# ---------------------------------------------------------------------------
# Tier A volume — Task 3
# ---------------------------------------------------------------------------


def _clv_series(highs, lows, closes):
    """Chaikin Line Value per bar: ``((close-low)-(high-close))/(high-low)``."""
    n = len(closes)
    out = [0.0] * n
    for i in range(n):
        rng = highs[i] - lows[i]
        if rng != 0:
            out[i] = ((closes[i] - lows[i]) - (highs[i] - closes[i])) / rng
    return out


@indicator(category="volume", inputs=["high", "low", "close", "volume"], params=[], outputs=["ad"])
def ad(highs, lows, closes, volumes):
    """Chaikin Accumulation/Distribution Line: cumulative ``CLV * volume``."""
    clv = _clv_series(highs, lows, closes)
    n = len(closes)
    out = [0.0] * n
    out[0] = clv[0] * volumes[0]
    for i in range(1, n):
        out[i] = out[i - 1] + clv[i] * volumes[i]
    return out


@indicator(category="volume", inputs=["high", "low", "close", "volume"], params=[Param("fast", "int", 3, 2, 50, 1), Param("slow", "int", 10, 2, 200, 1)], outputs=["adosc"])
def adosc(highs, lows, closes, volumes, fast: int = 3, slow: int = 10):
    """Chaikin A/D Oscillator: ``EMA(ad, fast) - EMA(ad, slow)``."""
    ad_line = ad(highs, lows, closes, volumes)
    ema_fast = ema(ad_line, fast)
    ema_slow = ema(ad_line, slow)
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(n):
        f, s = ema_fast[i], ema_slow[i]
        if f is not None and s is not None:
            out[i] = f - s
    return out


@indicator(category="volume", inputs=["high", "low", "close", "volume"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["cmf"])
def cmf(highs, lows, closes, volumes, period: int = 20):
    """Chaikin Money Flow: ``sum(CLV*vol, p) / sum(vol, p)`` rolling."""
    clv = _clv_series(highs, lows, closes)
    n = len(closes)
    out: list[float | None] = [None] * n
    run_clvv = 0.0
    run_v = 0.0
    for i in range(n):
        run_clvv += clv[i] * volumes[i]
        run_v += volumes[i]
        if i >= period:
            run_clvv -= clv[i - period] * volumes[i - period]
            run_v -= volumes[i - period]
        if i >= period - 1:
            out[i] = run_clvv / run_v if run_v != 0 else None
    return out


@indicator(category="volume", inputs=["close", "volume"], params=[Param("period", "int", 13, 2, 200, 1)], outputs=["efi"])
def efi(closes, volumes, period: int = 13):
    """Elder Force Index: ``EMA((close[i]-close[i-1])*volume[i], period)``."""
    n = len(closes)
    # Force values are only defined from index 1 onward (no prior close at i=0).
    # Build the defined tail and EMA over it, then scatter back to aligned output
    # to avoid polluting the EMA seed with the synthetic raw[0]=0.0.
    raw: list[float | None] = [None] * n
    for i in range(1, n):
        raw[i] = (closes[i] - closes[i - 1]) * volumes[i]
    defined = [(i, v) for i, v in enumerate(raw) if v is not None]
    out: list[float | None] = [None] * n
    if len(defined) >= period:
        e = ema([v for _, v in defined], period)
        for (i, _), ev in zip(defined, e, strict=True):
            out[i] = ev
    return out


@indicator(category="volume", inputs=["close", "volume"], params=[], outputs=["pvt"])
def pvt(closes, volumes):
    """Price Volume Trend: cumulative ``pvt[i-1] + vol[i]*(close[i]-close[i-1])/close[i-1]``."""
    n = len(closes)
    out = [0.0] * n
    for i in range(1, n):
        rocp_val = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0.0
        out[i] = out[i - 1] + volumes[i] * rocp_val
    return out


@indicator(category="volume", inputs=["high", "low", "volume"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["eom"])
def eom(highs, lows, volumes, period: int = 14):
    """Ease of Movement: ``SMA( midpoint_move / (volume/(high-low)), period )``."""
    n = len(highs)
    raw: list[float | None] = [None] * n
    for i in range(1, n):
        mid_move = ((highs[i] + lows[i]) / 2.0) - ((highs[i - 1] + lows[i - 1]) / 2.0)
        hl = highs[i] - lows[i]
        if hl != 0 and volumes[i] != 0:
            box_ratio = volumes[i] / hl
            raw[i] = mid_move / box_ratio
        else:
            raw[i] = 0.0
    # SMA of defined raw tail
    defined = [(i, v) for i, v in enumerate(raw) if v is not None]
    out: list[float | None] = [None] * n
    if len(defined) >= period:
        sm = sma([v for _, v in defined], period)
        for (i, _), sv in zip(defined, sm, strict=True):
            out[i] = sv
    return out


@indicator(category="volume", inputs=["close", "volume"], params=[], outputs=["nvi"])
def nvi(closes, volumes):
    """Negative Volume Index: starts at 1000; updates only when volume decreases."""
    n = len(closes)
    out = [1000.0] * n
    for i in range(1, n):
        if volumes[i] < volumes[i - 1]:
            rocp_val = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0.0
            out[i] = out[i - 1] * (1.0 + rocp_val)
        else:
            out[i] = out[i - 1]
    return out


@indicator(category="volume", inputs=["close", "volume"], params=[], outputs=["pvi"])
def pvi(closes, volumes):
    """Positive Volume Index: starts at 1000; updates only when volume increases."""
    n = len(closes)
    out = [1000.0] * n
    for i in range(1, n):
        if volumes[i] > volumes[i - 1]:
            rocp_val = (closes[i] - closes[i - 1]) / closes[i - 1] if closes[i - 1] != 0 else 0.0
            out[i] = out[i - 1] * (1.0 + rocp_val)
        else:
            out[i] = out[i - 1]
    return out
