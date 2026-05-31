import math

from .base import Param, indicator
from .overlap import ema, sma, wma


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


# ---------------------------------------------------------------------------
# Tier B momentum — Task 2 (11 indicators)
# ---------------------------------------------------------------------------


@indicator(category="momentum", inputs=["high", "low"], params=[], outputs=["ao"])
def ao(highs, lows):
    """Awesome Oscillator: ``SMA(median, 5) - SMA(median, 34)``, median = (H+L)/2."""
    n = len(highs)
    median = [(highs[i] + lows[i]) / 2.0 for i in range(n)]
    sma5  = sma(median, 5)
    sma34 = sma(median, 34)
    out: list[float | None] = [None] * n
    for i in range(n):
        if sma5[i] is not None and sma34[i] is not None:
            out[i] = sma5[i] - sma34[i]
    return out


@indicator(category="momentum", inputs=["high", "low"], params=[], outputs=["ac"])
def ac(highs, lows):
    """Accelerator Oscillator: ``AO - SMA(AO, 5)``."""
    n = len(highs)
    ao_vals = ao(highs, lows)
    # compute SMA-5 of the defined AO tail, mapped back to aligned positions
    defined = [(i, v) for i, v in enumerate(ao_vals) if v is not None]
    sma5: list[float | None] = [None] * n
    if len(defined) >= 5:
        sm = sma([v for _, v in defined], 5)
        for (i, _), sv in zip(defined, sm, strict=True):
            sma5[i] = sv
    out: list[float | None] = [None] * n
    for i in range(n):
        if ao_vals[i] is not None and sma5[i] is not None:
            out[i] = ao_vals[i] - sma5[i]
    return out


@indicator(
    category="momentum",
    inputs=["high", "low"],
    params=[Param("period", "int", 9, 2, 100, 1)],
    outputs=["fisher", "trigger"],
)
def fisher(highs, lows, period: int = 9):
    """Fisher Transform: normalises the (H+L)/2 position within its recent range
    and applies ``0.5 * ln((1+v)/(1-v))``, smoothed; trigger = fisher[i-1]."""
    n = len(highs)
    fish: list[float | None] = [None] * n
    trig: list[float | None] = [None] * n
    if n < period:
        return fish, trig
    median = [(highs[i] + lows[i]) / 2.0 for i in range(n)]
    prev_value = 0.0
    prev_fish  = 0.0
    for i in range(period - 1, n):
        hi = max(median[i - period + 1 : i + 1])
        lo = min(median[i - period + 1 : i + 1])
        rng = hi - lo
        if rng == 0.0:
            norm = 0.0
        else:
            norm = (median[i] - lo) / rng  # [0, 1]
        # compress to (-1, 1) with memory
        value = 0.66 * (2.0 * norm - 1.0) + 0.67 * prev_value
        value = max(-0.999, min(0.999, value))
        f = 0.5 * math.log((1.0 + value) / (1.0 - value)) + 0.5 * prev_fish
        fish[i]    = f
        trig[i]    = prev_fish if i > period - 1 else None
        prev_value = value
        prev_fish  = f
    # first defined bar has no previous fisher → trigger is None there
    if period - 1 < n:
        trig[period - 1] = None
    return fish, trig


@indicator(
    category="momentum",
    inputs=["close"],
    params=[
        Param("rsi_p",    "int", 3,   2, 100, 1),
        Param("streak_p", "int", 2,   2, 100, 1),
        Param("rank_p",   "int", 100, 10, 500, 1),
    ],
    outputs=["crsi"],
)
def connors_rsi(values, rsi_p: int = 3, streak_p: int = 2, rank_p: int = 100):
    """ConnorsRSI = (RSI(close,3) + RSI(streak,2) + PercentRank(ROC(1),100)) / 3.

    *streak* = consecutive up/down days count (positive for up-streaks,
    negative for down-streaks, 0 on flat).
    *PercentRank* = percentage of the last ``rank_p`` ROC values that are
    strictly less than the current value.
    """
    n = len(values)
    # --- streak series ---
    streak: list[float] = [0.0] * n
    for i in range(1, n):
        diff = values[i] - values[i - 1]
        if diff > 0:
            streak[i] = streak[i - 1] + 1.0 if streak[i - 1] > 0 else 1.0
        elif diff < 0:
            streak[i] = streak[i - 1] - 1.0 if streak[i - 1] < 0 else -1.0
        else:
            streak[i] = 0.0

    # --- component 1: RSI(close, rsi_p) ---
    rsi1 = rsi(values, rsi_p)

    # --- component 2: RSI(streak, streak_p) ---
    rsi2 = rsi(streak, streak_p)

    # --- component 3: PercentRank of ROC(1) over rank_p ---
    # roc returns percent, but for percentrank we just need the direction of each value
    roc1: list[float | None] = [None] * n
    for i in range(1, n):
        if values[i - 1] != 0:
            roc1[i] = (values[i] / values[i - 1] - 1.0) * 100.0

    prank: list[float | None] = [None] * n
    for i in range(rank_p, n):
        cur = roc1[i]
        if cur is None:
            continue
        window = [roc1[j] for j in range(i - rank_p + 1, i + 1) if roc1[j] is not None]
        if len(window) < 1:
            continue
        count_below = sum(1 for v in window if v < cur)
        prank[i] = 100.0 * count_below / len(window)

    out: list[float | None] = [None] * n
    for i in range(n):
        r1, r2, pr = rsi1[i], rsi2[i], prank[i]
        if r1 is not None and r2 is not None and pr is not None:
            out[i] = (r1 + r2 + pr) / 3.0
    return out


@indicator(
    category="momentum",
    inputs=["close"],
    params=[
        Param("wma_p",     "int", 10, 2, 100, 1),
        Param("roc_long",  "int", 14, 2, 200, 1),
        Param("roc_short", "int", 11, 2, 200, 1),
    ],
    outputs=["coppock"],
)
def coppock(values, wma_p: int = 10, roc_long: int = 14, roc_short: int = 11):
    """Coppock Curve: ``WMA(ROC(close, roc_long) + ROC(close, roc_short), wma_p)``."""
    n = len(values)
    roc_l = roc(values, roc_long)
    roc_s = roc(values, roc_short)
    combined: list[float | None] = [None] * n
    for i in range(n):
        if roc_l[i] is not None and roc_s[i] is not None:
            combined[i] = roc_l[i] + roc_s[i]
    # WMA of the defined combined tail, mapped back
    defined = [(i, v) for i, v in enumerate(combined) if v is not None]
    out: list[float | None] = [None] * n
    if len(defined) >= wma_p:
        wma_vals = wma([v for _, v in defined], wma_p)
        for (i, _), wv in zip(defined, wma_vals, strict=True):
            out[i] = wv
    return out


@indicator(
    category="momentum",
    inputs=["high", "low", "close"],
    params=[Param("period", "int", 13, 2, 200, 1)],
    outputs=["bull_power", "bear_power"],
)
def elder_ray(highs, lows, closes, period: int = 13):
    """Elder Ray Index: ``bull_power = high - EMA(close, p)``,
    ``bear_power = low - EMA(close, p)``."""
    ema_c = ema(closes, period)
    n = len(closes)
    bull: list[float | None] = [None] * n
    bear: list[float | None] = [None] * n
    for i in range(n):
        if ema_c[i] is not None:
            bull[i] = highs[i] - ema_c[i]
            bear[i] = lows[i]  - ema_c[i]
    return bull, bear


def _swma(values: list[float]) -> list[float | None]:
    """Symmetric Weighted Moving Average [1,2,2,1]/6 over 4 bars (pure, unregistered helper)."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(3, n):
        out[i] = (values[i - 3] + 2.0 * values[i - 2] + 2.0 * values[i - 1] + values[i]) / 6.0
    return out


@indicator(
    category="momentum",
    inputs=["open", "high", "low", "close"],
    params=[Param("period", "int", 10, 2, 200, 1)],
    outputs=["rvgi", "signal"],
)
def relative_vigor(opens, highs, lows, closes, period: int = 10):
    """Relative Vigor Index (RVGI): SWMA-smoothed close-open / SWMA-smoothed H-L
    summed over ``period``; signal = 4-bar SWMA of rvgi."""
    n = len(closes)
    co = [closes[i] - opens[i] for i in range(n)]
    hl = [highs[i] - lows[i]   for i in range(n)]
    num_sw = _swma(co)   # SWMA of (close - open)
    den_sw = _swma(hl)   # SWMA of (high  - low)

    rvgi_line: list[float | None] = [None] * n
    for i in range(period - 1 + 3, n):   # +3 because swma needs 4 bars
        num_sum = 0.0
        den_sum = 0.0
        valid = True
        for k in range(i - period + 1, i + 1):
            if num_sw[k] is None or den_sw[k] is None:
                valid = False
                break
            num_sum += num_sw[k]
            den_sum += den_sw[k]
        if valid and den_sum != 0.0:
            rvgi_line[i] = num_sum / den_sum

    # signal = 4-bar SWMA of rvgi (mapped back using the defined tail pattern)
    defined = [(i, v) for i, v in enumerate(rvgi_line) if v is not None]
    sig: list[float | None] = [None] * n
    if len(defined) >= 4:
        sig_raw = _swma([v for _, v in defined])
        for (i, _), sv in zip(defined, sig_raw, strict=True):
            sig[i] = sv
    return rvgi_line, sig


@indicator(
    category="momentum",
    inputs=["close"],
    params=[
        Param("long",   "int", 20, 2, 400, 1),
        Param("short",  "int", 5,  2, 200, 1),
        Param("signal", "int", 5,  2, 200, 1),
    ],
    outputs=["smi", "signal"],
)
def smi_ergodic(values, long: int = 20, short: int = 5, signal: int = 5):
    """SMI Ergodic Indicator: TSI pattern with (long, short) double-EMA smoothing;
    signal = EMA(smi, signal_period). Range approximately ±100."""
    n = len(values)
    delta      = [0.0] + [values[i] - values[i - 1] for i in range(1, n)]
    abs_delta  = [abs(d) for d in delta]

    # double-EMA of delta and abs_delta using the map-back pattern
    def _double_ema(src, p1, p2):
        e1 = ema(src, p1)
        defined1 = [(i, v) for i, v in enumerate(e1) if v is not None]
        e2: list[float | None] = [None] * n
        if len(defined1) >= p2:
            e2_vals = ema([v for _, v in defined1], p2)
            for (i, _), ev in zip(defined1, e2_vals, strict=True):
                e2[i] = ev
        return e2

    ema2_d = _double_ema(delta,     long, short)
    ema2_a = _double_ema(abs_delta, long, short)

    smi_line: list[float | None] = [None] * n
    for i in range(n):
        d_val, a_val = ema2_d[i], ema2_a[i]
        if d_val is not None and a_val is not None and a_val != 0:
            smi_line[i] = 100.0 * d_val / a_val

    # signal = EMA of smi_line, mapped back
    defined_smi = [(i, v) for i, v in enumerate(smi_line) if v is not None]
    sig_line: list[float | None] = [None] * n
    if len(defined_smi) >= signal:
        sig_vals = ema([v for _, v in defined_smi], signal)
        for (i, _), sv in zip(defined_smi, sig_vals, strict=True):
            sig_line[i] = sv

    return smi_line, sig_line


@indicator(
    category="momentum",
    inputs=["high", "low", "close"],
    params=[Param("period", "int", 14, 2, 200, 1)],
    outputs=["vi_plus", "vi_minus"],
)
def vortex(highs, lows, closes, period: int = 14):
    """Vortex Indicator: ``vi_plus = sum(|H[i]-L[i-1]|, p) / sum(TR, p)``,
    ``vi_minus = sum(|L[i]-H[i-1]|, p) / sum(TR, p)``."""
    n = len(closes)
    # import locally to avoid circular-import at module load
    from .volatility import true_range as _true_range
    trs = _true_range(highs, lows, closes)
    vm_plus:  list[float] = [0.0] * n
    vm_minus: list[float] = [0.0] * n
    for i in range(1, n):
        vm_plus[i]  = abs(highs[i] - lows[i - 1])
        vm_minus[i] = abs(lows[i]  - highs[i - 1])

    vi_p: list[float | None] = [None] * n
    vi_m: list[float | None] = [None] * n
    for i in range(period, n):
        sum_tr  = sum(trs[i - period + 1 : i + 1])
        sum_vp  = sum(vm_plus[i  - period + 1 : i + 1])
        sum_vm  = sum(vm_minus[i - period + 1 : i + 1])
        if sum_tr > 0:
            vi_p[i] = sum_vp / sum_tr
            vi_m[i] = sum_vm / sum_tr
    return vi_p, vi_m


@indicator(
    category="momentum",
    inputs=["high", "low", "close"],
    params=[
        Param("p", "int", 10, 2, 200,  1),
        Param("x", "int",  1, 1,  10,  1),
        Param("q", "int",  9, 2, 200,  1),
    ],
    outputs=["long_stop", "short_stop"],
)
def chande_kroll_stop(highs, lows, closes, p: int = 10, x: int = 1, q: int = 9):
    """Chande Kroll Stop: first_high = maxH(p) - x*ATR(p);
    first_low = minL(p) + x*ATR(p);
    long_stop  = rolling max(first_high, q);
    short_stop = rolling min(first_low, q)."""
    from .volatility import atr as _atr
    n = len(closes)
    atr_vals = _atr(highs, lows, closes, p)

    first_high: list[float | None] = [None] * n
    first_low:  list[float | None] = [None] * n
    for i in range(p - 1, n):
        if atr_vals[i] is None:
            continue
        hh = max(highs[i - p + 1 : i + 1])
        ll = min(lows[i  - p + 1 : i + 1])
        first_high[i] = hh - x * atr_vals[i]
        first_low[i]  = ll + x * atr_vals[i]

    long_stop:  list[float | None] = [None] * n
    short_stop: list[float | None] = [None] * n
    for i in range(q - 1, n):
        window_h = [first_high[j] for j in range(i - q + 1, i + 1) if first_high[j] is not None]
        window_l = [first_low[j]  for j in range(i - q + 1, i + 1) if first_low[j]  is not None]
        if window_h:
            long_stop[i]  = max(window_h)
        if window_l:
            short_stop[i] = min(window_l)
    return long_stop, short_stop


@indicator(
    category="momentum",
    inputs=["open", "high", "low", "close"],
    params=[Param("limit", "float", 1.0, 0.1, 10.0, 0.1)],
    outputs=["asi"],
)
def asi(opens, highs, lows, closes, limit: float = 1.0):
    """Wilder's Accumulative Swing Index (ASI) — cumulative sum of per-bar SI.

    Per-bar SI formula (Wilder, 1978):
        R  = largest of: |H-Cprev|, |L-Cprev|, |H-L|
        K  = max(|H-Cprev|, |L-Cprev|)
        T  = R + 0.25*|Cprev-Oprev| (modified R)
        SI = 50 * [(C - Cprev + 0.5*(C-O) + 0.25*(Cprev-Oprev)) / T] * (K/limit)
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    cum = 0.0
    for i in range(1, n):
        c  = closes[i];   cp = closes[i - 1]
        o  = opens[i];    op = opens[i - 1]
        h  = highs[i];    l  = lows[i]

        # R: greatest of |H-Cp|, |L-Cp|, |H-L|
        a = abs(h  - cp)
        b = abs(l  - cp)
        c2 = h - l
        r = max(a, b, c2)

        # K: max of first two
        k = max(a, b)

        if r == 0.0 or limit == 0.0:
            si = 0.0
        else:
            # modified R per Wilder: R + 0.25*|Cprev - Oprev|
            t = r + 0.25 * abs(cp - op)
            if t == 0.0:
                si = 0.0
            else:
                numerator = (c - cp) + 0.5 * (c - o) + 0.25 * (cp - op)
                si = 50.0 * (numerator / t) * (k / limit)

        cum    += si
        out[i]  = cum
    return out
