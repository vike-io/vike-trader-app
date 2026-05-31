import math

from .base import Param, indicator
from .overlap import ema, sma


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 14, 2, 100, 1)], outputs=["rsi"])
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


@indicator(category="momentum", inputs=["close"], params=[Param("fast", "int", 12, 2, 100, 1), Param("slow", "int", 26, 2, 200, 1), Param("signal", "int", 9, 2, 100, 1)], outputs=["macd", "signal", "hist"])
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


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("k_period", "int", 14, 2, 100, 1), Param("d_period", "int", 3, 1, 50, 1)], outputs=["k", "d"])
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


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("period", "int", 20, 2, 100, 1)], outputs=["cci"])
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


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("period", "int", 14, 2, 100, 1)], outputs=["williams_r"])
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


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 1, 1, 100, 1)], outputs=["roc"])
def roc(values, period: int = 1):
    """Rate of change in percent: ``(v[i]/v[i-period] - 1) * 100``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        out[i] = (values[i] / values[i - period] - 1.0) * 100.0 if values[i - period] else None
    return out


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("period", "int", 14, 2, 100, 1)], outputs=["adx", "plus_di", "minus_di"])
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


# ---------------------------------------------------------------------------
# Tier A momentum — batch 2 (Task 2)
# ---------------------------------------------------------------------------


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 10, 1, 400, 1)], outputs=["mom"])
def mom(values, period: int = 10):
    """Momentum: ``v[i] - v[i-period]``, aligned (``None`` warm-up)."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        out[i] = values[i] - values[i - period]
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 10, 1, 400, 1)], outputs=["rocp"])
def rocp(values, period: int = 10):
    """Rate of change percentage: ``(v[i]-v[i-p])/v[i-p]``, aligned."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        prev = values[i - period]
        out[i] = (values[i] - prev) / prev if prev != 0 else None
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 10, 1, 400, 1)], outputs=["rocr"])
def rocr(values, period: int = 10):
    """Rate of change ratio: ``v[i]/v[i-p]``, aligned."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        prev = values[i - period]
        out[i] = values[i] / prev if prev != 0 else None
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 10, 1, 400, 1)], outputs=["rocr100"])
def rocr100(values, period: int = 10):
    """Rate of change ratio x100: ``v[i]/v[i-p]*100``, aligned."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        prev = values[i - period]
        out[i] = values[i] / prev * 100.0 if prev != 0 else None
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("fast", "int", 12, 2, 200, 1), Param("slow", "int", 26, 2, 400, 1)], outputs=["ppo"])
def ppo(values, fast: int = 12, slow: int = 26):
    """Percentage Price Oscillator: ``(EMA(fast)-EMA(slow))/EMA(slow)*100``."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(n):
        f, s = ema_fast[i], ema_slow[i]
        if f is not None and s is not None and s != 0:
            out[i] = (f - s) / s * 100.0
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("fast", "int", 12, 2, 200, 1), Param("slow", "int", 26, 2, 400, 1)], outputs=["apo"])
def apo(values, fast: int = 12, slow: int = 26):
    """Absolute Price Oscillator: ``EMA(fast)-EMA(slow)``."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(n):
        f, s = ema_fast[i], ema_slow[i]
        if f is not None and s is not None:
            out[i] = f - s
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["cmo"])
def cmo(values, period: int = 14):
    """Chande Momentum Oscillator: ``100*(sumUp-sumDown)/(sumUp+sumDown)`` over rolling ``period``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        sum_up = 0.0
        sum_dn = 0.0
        for j in range(i - period + 1, i + 1):
            delta = values[j] - values[j - 1]
            if delta > 0:
                sum_up += delta
            else:
                sum_dn += -delta
        denom = sum_up + sum_dn
        out[i] = 100.0 * (sum_up - sum_dn) / denom if denom != 0 else 0.0
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 18, 2, 200, 1)], outputs=["trix"])
def trix(values, period: int = 18):
    """TRIX: 1-bar % ROC of ``EMA(EMA(EMA(close, p)))``, aligned."""
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
    n = len(values)
    out: list[float | None] = [None] * n
    prev_idx = None
    for i in range(n):
        if e3[i] is not None:
            if prev_idx is not None and e3[prev_idx] != 0:
                out[i] = (e3[i] - e3[prev_idx]) / e3[prev_idx] * 100.0
            prev_idx = i
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("long", "int", 25, 2, 400, 1), Param("short", "int", 13, 2, 200, 1)], outputs=["tsi"])
def tsi(values, long: int = 25, short: int = 13):
    """True Strength Index: ``100 * EMA(EMA(d,long),short) / EMA(EMA(|d|,long),short)``."""
    n = len(values)
    delta: list[float] = [0.0] + [values[i] - values[i - 1] for i in range(1, n)]
    abs_delta: list[float] = [abs(d) for d in delta]
    ema1_d = ema(delta, long)
    ema1_a = ema(abs_delta, long)

    def _ema_of_defined(src):
        defined = [(i, v) for i, v in enumerate(src) if v is not None]
        result: list[float | None] = [None] * n
        if len(defined) >= short:
            smoothed = ema([v for _, v in defined], short)
            for (i, _), sv in zip(defined, smoothed, strict=True):
                result[i] = sv
        return result

    ema2_d = _ema_of_defined(ema1_d)
    ema2_a = _ema_of_defined(ema1_a)
    out: list[float | None] = [None] * n
    for i in range(n):
        d_val, a_val = ema2_d[i], ema2_a[i]
        if d_val is not None and a_val is not None and a_val != 0:
            out[i] = 100.0 * d_val / a_val
    return out


@indicator(category="momentum", inputs=["close"], params=[Param("period", "int", 20, 2, 400, 1)], outputs=["dpo"])
def dpo(values, period: int = 20):
    """Detrended Price Oscillator: ``v[i-(p//2+1)] - SMA(p)[i]``, aligned."""
    n = len(values)
    out: list[float | None] = [None] * n
    shift = period // 2 + 1
    ma = sma(values, period)
    for i in range(period - 1, n):
        j = i - shift
        if j >= 0 and ma[i] is not None:
            out[i] = values[j] - ma[i]
    return out


@indicator(category="momentum", inputs=["high", "low"], params=[Param("period", "int", 14, 2, 400, 1)], outputs=["aroon_up", "aroon_down"])
def aroon(highs, lows, period: int = 14):
    """Aroon Up/Down: ``up=100*(p-bars_since_highest_high)/p``, analogous for down."""
    n = len(highs)
    up: list[float | None] = [None] * n
    down: list[float | None] = [None] * n
    for i in range(period, n):
        window_h = highs[i - period : i + 1]
        window_l = lows[i - period : i + 1]
        max_h = max(window_h)
        min_l = min(window_l)
        # find most recent bar (from right) that matches the extreme
        bars_since_hh = next(k for k in range(period + 1) if window_h[period - k] == max_h)
        bars_since_ll = next(k for k in range(period + 1) if window_l[period - k] == min_l)
        up[i]   = 100.0 * (period - bars_since_hh) / period
        down[i] = 100.0 * (period - bars_since_ll) / period
    return up, down


@indicator(category="momentum", inputs=["high", "low"], params=[Param("period", "int", 14, 2, 400, 1)], outputs=["aroonosc"])
def aroonosc(highs, lows, period: int = 14):
    """Aroon Oscillator: ``aroon_up - aroon_down``."""
    up, down = aroon(highs, lows, period)
    n = len(highs)
    out: list[float | None] = [None] * n
    for i in range(n):
        if up[i] is not None and down[i] is not None:
            out[i] = up[i] - down[i]
    return out


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("period", "int", 14, 2, 100, 1)], outputs=["adxr"])
def adxr(highs, lows, closes, period: int = 14):
    """Average Directional Movement Index Rating: ``(ADX[i] + ADX[i-p]) / 2``."""
    adx_line, _, _ = adx(highs, lows, closes, period)
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(period, n):
        a_now = adx_line[i]
        a_prev = adx_line[i - period]
        if a_now is not None and a_prev is not None:
            out[i] = (a_now + a_prev) / 2.0
    return out


@indicator(category="momentum", inputs=["open", "high", "low", "close"], params=[], outputs=["bop"])
def bop(opens, highs, lows, closes):
    """Balance of Power: ``(close-open)/(high-low)`` (0 if range is 0), no warm-up."""
    n = len(closes)
    out: list[float] = [0.0] * n
    for i in range(n):
        rng = highs[i] - lows[i]
        out[i] = (closes[i] - opens[i]) / rng if rng != 0 else 0.0
    return out


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("k", "int", 14, 2, 100, 1), Param("d", "int", 3, 1, 50, 1)], outputs=["k", "d"])
def stochf(highs, lows, closes, k: int = 14, d: int = 3):
    """Fast Stochastic: ``%K=100*(close-LL)/(HH-LL)``, ``%D=SMA(%K, d)``."""
    n = len(closes)
    k_line: list[float | None] = [None] * n
    for i in range(k - 1, n):
        hh = max(highs[i - k + 1 : i + 1])
        ll = min(lows[i - k + 1 : i + 1])
        rng = hh - ll
        k_line[i] = 100.0 * (closes[i] - ll) / rng if rng != 0 else 0.0
    defined = [(i, v) for i, v in enumerate(k_line) if v is not None]
    d_line: list[float | None] = [None] * n
    if len(defined) >= d:
        d_vals = sma([v for _, v in defined], d)
        for (i, _), dv in zip(defined, d_vals, strict=True):
            d_line[i] = dv
    for i in range(n):
        if i < k - 1 + d - 1:
            d_line[i] = None
    return k_line, d_line


@indicator(category="momentum", inputs=["close"], params=[Param("rsi_p", "int", 14, 2, 100, 1), Param("k", "int", 14, 2, 100, 1), Param("d", "int", 3, 1, 50, 1)], outputs=["k", "d"])
def stochrsi(values, rsi_p: int = 14, k: int = 14, d: int = 3):
    """Stochastic RSI: stochastic oscillator applied to the RSI series."""
    rsi_vals = rsi(values, rsi_p)
    n = len(values)
    k_line: list[float | None] = [None] * n
    for i in range(n):
        start = i - k + 1
        if start < 0:
            continue
        window = rsi_vals[start : i + 1]
        if any(v is None for v in window):
            continue
        hh = max(window)
        ll = min(window)
        rng = hh - ll
        k_line[i] = 100.0 * (rsi_vals[i] - ll) / rng if rng != 0 else 0.0
    defined = [(i, v) for i, v in enumerate(k_line) if v is not None]
    d_line: list[float | None] = [None] * n
    if len(defined) >= d:
        d_vals = sma([v for _, v in defined], d)
        for (i, _), dv in zip(defined, d_vals, strict=True):
            d_line[i] = dv
    return k_line, d_line


@indicator(category="momentum", inputs=["high", "low", "close"], params=[Param("p1", "int", 7, 2, 100, 1), Param("p2", "int", 14, 2, 200, 1), Param("p3", "int", 28, 2, 400, 1)], outputs=["ultosc"])
def ultosc(highs, lows, closes, p1: int = 7, p2: int = 14, p3: int = 28):
    """Ultimate Oscillator (Larry Williams): ``100*(4*A7+2*A14+A28)/7`` weighted buying pressure."""
    n = len(closes)
    out: list[float | None] = [None] * n
    bp: list[float] = [0.0] * n
    tr: list[float] = [0.0] * n
    for i in range(1, n):
        prev_c = closes[i - 1]
        true_low  = min(lows[i], prev_c)
        true_high = max(highs[i], prev_c)
        bp[i] = closes[i] - true_low
        tr[i] = true_high - true_low
    largest = max(p1, p2, p3)
    for i in range(largest, n):
        def _avg(p, idx=i):
            s_bp = sum(bp[idx - p + 1 : idx + 1])
            s_tr = sum(tr[idx - p + 1 : idx + 1])
            return s_bp / s_tr if s_tr != 0 else 0.0
        a1 = _avg(p1)
        a2 = _avg(p2)
        a3 = _avg(p3)
        out[i] = 100.0 * (4.0 * a1 + 2.0 * a2 + a3) / 7.0
    return out


@indicator(
    category="momentum",
    inputs=["close"],
    params=[
        Param("roc1", "int", 10, 1, 200, 1),
        Param("sma1", "int", 10, 2, 200, 1),
        Param("roc2", "int", 15, 1, 200, 1),
        Param("sma2", "int", 10, 2, 200, 1),
        Param("roc3", "int", 20, 1, 200, 1),
        Param("sma3", "int", 10, 2, 200, 1),
        Param("roc4", "int", 30, 1, 200, 1),
        Param("sma4", "int", 15, 2, 200, 1),
        Param("signal", "int", 9, 2, 200, 1),
    ],
    outputs=["kst", "signal"],
)
def kst(values, roc1: int = 10, sma1: int = 10, roc2: int = 15, sma2: int = 10,
        roc3: int = 20, sma3: int = 10, roc4: int = 30, sma4: int = 15,
        signal: int = 9):
    """Pring's Know Sure Thing (KST): weighted sum of 4 smoothed ROCs; signal = SMA(kst, 9)."""
    n = len(values)

    def _roc_sma(period_r, period_s, weight):
        roc_vals: list[float | None] = [None] * n
        for i in range(period_r, n):
            prev = values[i - period_r]
            roc_vals[i] = (values[i] - prev) / prev * 100.0 if prev != 0 else None
        defined = [(i, v) for i, v in enumerate(roc_vals) if v is not None]
        smoothed: list[float | None] = [None] * n
        if len(defined) >= period_s:
            sm = sma([v for _, v in defined], period_s)
            for (i, _), sv in zip(defined, sm, strict=True):
                smoothed[i] = sv
        return smoothed, weight

    r1, w1 = _roc_sma(roc1, sma1, 1)
    r2, w2 = _roc_sma(roc2, sma2, 2)
    r3, w3 = _roc_sma(roc3, sma3, 3)
    r4, w4 = _roc_sma(roc4, sma4, 4)

    kst_line: list[float | None] = [None] * n
    for i in range(n):
        v1, v2, v3, v4 = r1[i], r2[i], r3[i], r4[i]
        if all(v is not None for v in (v1, v2, v3, v4)):
            kst_line[i] = w1 * v1 + w2 * v2 + w3 * v3 + w4 * v4

    defined_kst = [(i, v) for i, v in enumerate(kst_line) if v is not None]
    sig_line: list[float | None] = [None] * n
    if len(defined_kst) >= signal:
        sig_vals = sma([v for _, v in defined_kst], signal)
        for (i, _), sv in zip(defined_kst, sig_vals, strict=True):
            sig_line[i] = sv

    return kst_line, sig_line
