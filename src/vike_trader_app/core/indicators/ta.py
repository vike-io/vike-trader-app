"""Technical indicators. Each returns a list aligned to the input (``None`` warm-up).

Price-only indicators take a ``values`` list; OHLC indicators take separate
``highs``/``lows``/``closes`` (and ``volumes``) lists. Multi-line indicators return
a tuple of aligned lists.
"""

import math


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


def rsi(values, period: int = 14):
    """Wilder's Relative Strength Index (0..100), ``None`` during warm-up."""
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        change = values[i] - values[i - 1]
        gains += max(change, 0.0)
        losses += max(-change, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        change = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def macd(values, fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: returns ``(macd_line, signal_line, histogram)`` aligned to ``values``."""
    ema_fast, ema_slow = ema(values, fast), ema(values, slow)
    line = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow, strict=True)
    ]
    # signal = EMA of the defined MACD-line tail, mapped back to aligned positions
    defined = [(i, v) for i, v in enumerate(line) if v is not None]
    sig: list[float | None] = [None] * len(values)
    if len(defined) >= signal:
        sig_vals = ema([v for _, v in defined], signal)
        for (i, _), sv in zip(defined, sig_vals, strict=True):
            sig[i] = sv
    hist = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(line, sig, strict=True)
    ]
    return line, sig, hist


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


def stochastic(highs, lows, closes, k_period: int = 14, d_period: int = 3):
    """Stochastic oscillator: returns ``(%K, %D)`` aligned to the input."""
    n = len(closes)
    k: list[float | None] = [None] * n
    for i in range(k_period - 1, n):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        rng = hh - ll
        k[i] = 100.0 if rng == 0 else 100.0 * (closes[i] - ll) / rng
    d = sma([v if v is not None else 0.0 for v in k], d_period)
    # mask %D positions that depend on warm-up %K values
    for i in range(n):
        if i < k_period - 1 + d_period - 1:
            d[i] = None
    return k, d


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


def wma(values, period: int):
    """Weighted moving average (linear weights, recent heaviest)."""
    n = len(values)
    out: list[float | None] = [None] * n
    denom = period * (period + 1) / 2.0
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        out[i] = sum((k + 1) * window[k] for k in range(period)) / denom
    return out


def roc(values, period: int = 1):
    """Rate of change in percent: ``(v[i]/v[i-period] - 1) * 100``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        out[i] = (values[i] / values[i - period] - 1.0) * 100.0 if values[i - period] else None
    return out


def true_range(highs, lows, closes):
    """True range, aligned (``TR[0] = high - low``; later bars are gap-aware)."""
    n = len(closes)
    out = [highs[0] - lows[0]] + [0.0] * (n - 1)
    for i in range(1, n):
        out[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    return out


def williams_r(highs, lows, closes, period: int = 14):
    """Williams %R in [-100, 0] (0 = close at period high, -100 = at period low)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        hh = max(highs[i - period + 1 : i + 1])
        ll = min(lows[i - period + 1 : i + 1])
        rng = hh - ll
        out[i] = 0.0 if rng == 0 else -100.0 * (hh - closes[i]) / rng
    return out


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


def cci(highs, lows, closes, period: int = 20):
    """Commodity Channel Index, aligned (``None`` warm-up)."""
    n = len(closes)
    out: list[float | None] = [None] * n
    tp = [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(n)]
    for i in range(period - 1, n):
        window = tp[i - period + 1 : i + 1]
        ma = sum(window) / period
        mean_dev = sum(abs(x - ma) for x in window) / period
        out[i] = 0.0 if mean_dev == 0 else (tp[i] - ma) / (0.015 * mean_dev)
    return out


def adx(highs, lows, closes, period: int = 14):
    """Average Directional Index (Wilder): returns ``(adx, +DI, -DI)`` aligned."""
    n = len(closes)
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    adx_line: list[float | None] = [None] * n
    if n <= period:
        return adx_line, plus_di, minus_di
    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
    atr_s = sum(tr[1 : period + 1])
    pdm_s = sum(pdm[1 : period + 1])
    mdm_s = sum(mdm[1 : period + 1])
    dx_list = []
    for i in range(period, n):
        if i > period:
            atr_s += tr[i] - atr_s / period
            pdm_s += pdm[i] - pdm_s / period
            mdm_s += mdm[i] - mdm_s / period
        pdi = 100.0 * pdm_s / atr_s if atr_s > 0 else 0.0
        mdi = 100.0 * mdm_s / atr_s if atr_s > 0 else 0.0
        plus_di[i] = pdi
        minus_di[i] = mdi
        denom = pdi + mdi
        dx_list.append((i, 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0))
    if len(dx_list) >= period:
        prev = sum(d for _, d in dx_list[:period]) / period
        adx_line[dx_list[period - 1][0]] = prev
        for k in range(period, len(dx_list)):
            i, dxv = dx_list[k]
            prev = (prev * (period - 1) + dxv) / period
            adx_line[i] = prev
    return adx_line, plus_di, minus_di


def expand(fn, values, periods):
    """Indicator factory: run ``fn(values, p)`` for each ``p`` -> ``{p: result}``."""
    return {p: fn(values, p) for p in periods}


def from_talib(name: str, *args, **kwargs):  # pragma: no cover - optional bridge
    """Optional bridge to TA-Lib for any function not shipped natively.

    vike-trader-app ships a broad native set so TA-Lib is not required; install it
    (``pip install TA-Lib``) only to reach the long tail of its ~150 functions.
    """
    try:
        import talib
    except ImportError as exc:
        raise RuntimeError(
            "TA-Lib is not installed. vike-trader-app's native indicators cover the common set; "
            "install TA-Lib to bridge the rest: pip install TA-Lib"
        ) from exc
    return getattr(talib, name)(*args, **kwargs)
