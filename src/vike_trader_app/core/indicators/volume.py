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


# ---------------------------------------------------------------------------
# Tier B volume — Task 3
# ---------------------------------------------------------------------------


@indicator(
    category="volume",
    inputs=["close", "volume"],
    params=[],
    outputs=["net_volume"],
)
def net_volume(closes, volumes):
    """Signed (non-cumulative) volume per bar.

    ``+volume`` when close rises vs previous close,
    ``-volume`` when close falls,
    ``0`` when unchanged or at bar 0 (no prior close).
    """
    n = len(closes)
    out = [0.0] * n
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out[i] = volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = -volumes[i]
        # else unchanged → 0.0 already
    return out


@indicator(
    category="volume",
    inputs=["volume"],
    params=[
        Param("short", "int", 5, 2, 50, 1),
        Param("long", "int", 10, 2, 200, 1),
    ],
    outputs=["volume_osc"],
)
def volume_osc(volumes, short: int = 5, long: int = 10):
    """Volume Oscillator: ``(EMA(vol, short) - EMA(vol, long)) / EMA(vol, long) * 100``.

    Returns None during warm-up (until the slow EMA is seeded).
    """
    ema_short = ema(volumes, short)
    ema_long = ema(volumes, long)
    n = len(volumes)
    out: list[float | None] = [None] * n
    for i in range(n):
        s, l = ema_short[i], ema_long[i]
        if s is not None and l is not None and l != 0.0:
            out[i] = (s - l) / l * 100.0
    return out


@indicator(
    category="volume",
    inputs=["high", "low", "close", "volume"],
    params=[
        Param("fast", "int", 34, 2, 200, 1),
        Param("slow", "int", 55, 2, 500, 1),
        Param("signal", "int", 13, 2, 100, 1),
    ],
    outputs=["kvo", "signal"],
)
def kvo(highs, lows, closes, volumes, fast: int = 34, slow: int = 55, signal: int = 13):
    """Klinger Volume Oscillator.

    Trend direction ``t = +1`` when HLC3 rises, ``-1`` when it falls.
    Volume Force (simplified): ``vf = volume * t``.
    ``kvo = EMA(vf, fast) - EMA(vf, slow)``; ``signal = EMA(kvo, signal_period)``.

    (Simplified VF = volume * t; the cm bookkeeping version is numerically
    equivalent in directional signal and avoids the edge-case of cm=0 on
    trend reversals.)
    """
    n = len(closes)

    # Step 1: compute per-bar volume force
    vf_raw: list[float | None] = [None] * n
    for i in range(1, n):
        hlc3_cur = (highs[i] + lows[i] + closes[i]) / 3.0
        hlc3_prev = (highs[i - 1] + lows[i - 1] + closes[i - 1]) / 3.0
        t = 1.0 if hlc3_cur > hlc3_prev else -1.0
        vf_raw[i] = volumes[i] * t

    # Step 2: EMA of vf over the defined tail
    defined = [(i, v) for i, v in enumerate(vf_raw) if v is not None]
    ema_fast_full: list[float | None] = [None] * n
    ema_slow_full: list[float | None] = [None] * n
    if len(defined) >= slow:
        vf_vals = [v for _, v in defined]
        ef = ema(vf_vals, fast)
        es = ema(vf_vals, slow)
        for (idx, _), ef_v, es_v in zip(defined, ef, es):
            ema_fast_full[idx] = ef_v
            ema_slow_full[idx] = es_v

    # Step 3: kvo = fast EMA - slow EMA
    kvo_raw: list[float | None] = [None] * n
    for i in range(n):
        f, s = ema_fast_full[i], ema_slow_full[i]
        if f is not None and s is not None:
            kvo_raw[i] = f - s

    # Step 4: signal = EMA(kvo, signal_period) over defined kvo tail
    kvo_defined = [(i, v) for i, v in enumerate(kvo_raw) if v is not None]
    signal_full: list[float | None] = [None] * n
    if len(kvo_defined) >= signal:
        kvo_vals = [v for _, v in kvo_defined]
        sig_ema = ema(kvo_vals, signal)
        for (idx, _), sv in zip(kvo_defined, sig_ema):
            signal_full[idx] = sv

    return kvo_raw, signal_full
