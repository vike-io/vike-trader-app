"""Statistics indicators: linear regression family, variance, beta, correlation,
z-score, skewness, kurtosis, and mean absolute deviation.

All functions return input-aligned ``list[float | None]`` with ``None`` during
warm-up.  Multi-output functions return a tuple of aligned lists.

Shared helper ``_ols(window)`` computes slope ``b`` and intercept ``a`` for a
rolling OLS over x = 0 .. p-1.
"""

import math

from .base import Param, indicator


# ── shared OLS helper ─────────────────────────────────────────────────────────

def _ols(window: list[float]) -> tuple[float, float]:
    """Return ``(slope, intercept)`` of OLS fit over ``x = 0..p-1``.

    Uses the closed-form solution:
        b = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)
        a = (Σy − b·Σx) / n
    """
    p = len(window)
    sx = p * (p - 1) / 2.0          # Σ x  where x = 0,1,..,p-1
    sx2 = p * (p - 1) * (2 * p - 1) / 6.0  # Σ x²
    sy = sum(window)
    sxy = sum(i * window[i] for i in range(p))
    denom = p * sx2 - sx * sx
    if denom == 0.0:
        return 0.0, sy / p
    b = (p * sxy - sx * sy) / denom
    a = (sy - b * sx) / p
    return b, a


# ── linear regression family ──────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["linearreg"])
def linearreg(values, period: int = 14):
    """Linear regression value at the last point of each window: ``a + b*(p-1)``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        b, a = _ols(values[i - period + 1 : i + 1])
        out[i] = a + b * (period - 1)
    return out


@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["slope"])
def linearreg_slope(values, period: int = 14):
    """Rolling OLS slope ``b`` over each window of length ``period``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        b, _ = _ols(values[i - period + 1 : i + 1])
        out[i] = b
    return out


@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["angle"])
def linearreg_angle(values, period: int = 14):
    """Rolling OLS slope expressed in degrees: ``degrees(atan(b))``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        b, _ = _ols(values[i - period + 1 : i + 1])
        out[i] = math.degrees(math.atan(b))
    return out


@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["intercept"])
def linearreg_intercept(values, period: int = 14):
    """Rolling OLS intercept ``a`` (value of the regression line at x=0)."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        _, a = _ols(values[i - period + 1 : i + 1])
        out[i] = a
    return out


@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 14, 2, 200, 1)], outputs=["tsf"])
def tsf(values, period: int = 14):
    """Time Series Forecast: regression projected one step ahead (``a + b*p``)."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        b, a = _ols(values[i - period + 1 : i + 1])
        out[i] = a + b * period
    return out


# ── variance ──────────────────────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["var"])
def var(values, period: int = 20):
    """Rolling population variance over ``period``."""
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
            out[i] = max(run_sum2 / period - mean * mean, 0.0)
    return out


# ── beta ──────────────────────────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close", "benchmark"], params=[Param("period", "int", 5, 2, 200, 1)], outputs=["beta"])
def beta(closes, benchmarks, period: int = 5):
    """Rolling beta of ``close`` vs ``benchmark`` (``cov(rA, rB) / var(rB)``) over returns.

    Both ``closes`` and ``benchmarks`` must be provided in the data dict.
    ``beta`` requires at least ``period + 1`` bars (one extra for returns).
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    # compute return series (length n, first element None)
    ret_a: list[float | None] = [None] * n
    ret_b: list[float | None] = [None] * n
    for i in range(1, n):
        if closes[i - 1] != 0.0:
            ret_a[i] = (closes[i] - closes[i - 1]) / closes[i - 1]
        if benchmarks[i - 1] != 0.0:
            ret_b[i] = (benchmarks[i] - benchmarks[i - 1]) / benchmarks[i - 1]
    # rolling window of period returns (need indices where both are defined)
    # collect all (ra, rb) pairs
    pairs: list[tuple[float, float]] = [(None, None)] * n  # type: ignore[assignment]
    for i in range(n):
        if ret_a[i] is not None and ret_b[i] is not None:
            pairs[i] = (ret_a[i], ret_b[i])
    buf_a: list[float] = []
    buf_b: list[float] = []
    for i in range(n):
        ra, rb = pairs[i] if pairs[i] != (None, None) else (None, None)
        if ra is not None and rb is not None:
            buf_a.append(ra)
            buf_b.append(rb)
        else:
            # gap: reset or skip — insert a sentinel to keep alignment
            # We flush the buffer since we have a non-return gap
            buf_a.clear()
            buf_b.clear()
            continue
        if len(buf_a) > period:
            buf_a.pop(0)
            buf_b.pop(0)
        if len(buf_a) == period:
            mean_a = sum(buf_a) / period
            mean_b = sum(buf_b) / period
            cov = sum((buf_a[j] - mean_a) * (buf_b[j] - mean_b) for j in range(period)) / period
            vb = sum((buf_b[j] - mean_b) ** 2 for j in range(period)) / period
            if vb != 0.0:
                out[i] = cov / vb
    return out


# ── correlation ───────────────────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close", "benchmark"], params=[Param("period", "int", 30, 2, 200, 1)], outputs=["correl"])
def correl(closes, benchmarks, period: int = 30):
    """Rolling Pearson correlation of ``close`` and ``benchmark`` over ``period``.

    Both ``closes`` and ``benchmarks`` must be provided in the data dict.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    run_a = 0.0
    run_b = 0.0
    run_a2 = 0.0
    run_b2 = 0.0
    run_ab = 0.0
    for i in range(n):
        a, b = closes[i], benchmarks[i]
        run_a += a
        run_b += b
        run_a2 += a * a
        run_b2 += b * b
        run_ab += a * b
        if i >= period:
            oa = closes[i - period]
            ob = benchmarks[i - period]
            run_a -= oa
            run_b -= ob
            run_a2 -= oa * oa
            run_b2 -= ob * ob
            run_ab -= oa * ob
        if i >= period - 1:
            p = period
            num = p * run_ab - run_a * run_b
            denom = math.sqrt(max((p * run_a2 - run_a * run_a) * (p * run_b2 - run_b * run_b), 0.0))
            if denom != 0.0:
                out[i] = max(-1.0, min(1.0, num / denom))
    return out


# ── z-score ───────────────────────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["zscore"])
def zscore(values, period: int = 20):
    """Rolling z-score: ``(close - SMA(period)) / stddev(period)``."""
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
            var_val = run_sum2 / period - mean * mean
            sd = math.sqrt(max(var_val, 0.0))
            if sd != 0.0:
                out[i] = (values[i] - mean) / sd
            # else leave as None (zero stddev → undefined z-score)
    return out


# ── skewness ──────────────────────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["skew"])
def skew(values, period: int = 20):
    """Rolling sample skewness over ``period``.

    Uses the Fisher–Pearson standardized moment coefficient (sample, n-corrected):
        skew = (n / ((n-1)*(n-2))) * Σ((x - mean) / std)^3
    For ``period < 3``, returns None (undefined for sample skewness).
    """
    n = len(values)
    out: list[float | None] = [None] * n
    if period < 3:
        return out
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        p = period
        mean = sum(window) / p
        diffs = [x - mean for x in window]
        m2 = sum(d * d for d in diffs) / p
        if m2 == 0.0:
            out[i] = 0.0
            continue
        sd = math.sqrt(m2)
        m3 = sum(d ** 3 for d in diffs) / p
        # sample skewness (bias-corrected)
        out[i] = (m3 / (sd ** 3)) * (p * p / ((p - 1) * (p - 2)))
    return out


# ── kurtosis ──────────────────────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["kurtosis"])
def kurtosis(values, period: int = 20):
    """Rolling excess kurtosis (Fisher definition, sample-corrected) over ``period``.

    Uses the unbiased excess kurtosis estimator:
        kurt = n(n+1)/((n-1)(n-2)(n-3)) * Σ((x-μ)/σ)^4 − 3(n-1)^2/((n-2)(n-3))
    For ``period < 4``, returns None.
    """
    n = len(values)
    out: list[float | None] = [None] * n
    if period < 4:
        return out
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        p = period
        mean = sum(window) / p
        diffs = [x - mean for x in window]
        m2 = sum(d * d for d in diffs) / p
        if m2 == 0.0:
            out[i] = 0.0
            continue
        sd = math.sqrt(m2)
        m4 = sum(d ** 4 for d in diffs) / p
        # population kurtosis (using population sd — correct for this form of G2)
        pop_kurt = m4 / (sd ** 4)
        # unbiased excess kurtosis G2 (Fisher definition, scipy kurtosis(bias=False) / Excel KURT):
        # G2 = (p+1)*(p-1)/((p-2)*(p-3)) * pop_kurt  -  3*(p-1)^2/((p-2)*(p-3))
        denom = (p - 2) * (p - 3)
        out[i] = (p + 1) * (p - 1) / denom * pop_kurt - 3.0 * (p - 1) ** 2 / denom if denom != 0.0 else None
    return out


# ── mean absolute deviation ───────────────────────────────────────────────────

@indicator(category="statistics", inputs=["close"], params=[Param("period", "int", 20, 2, 200, 1)], outputs=["mad"])
def mad(values, period: int = 20):
    """Rolling mean absolute deviation: ``mean(|x - mean(window)|)`` over ``period``."""
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        mean = sum(window) / period
        out[i] = sum(abs(x - mean) for x in window) / period
    return out


# ── Tier B statistics — Task 4 ────────────────────────────────────────────────


@indicator(
    category="statistics",
    inputs=["close"],
    params=[Param("period", "int", 20, 2, 200, 1)],
    outputs=["std_error"],
)
def std_error(values, period: int = 20):
    """Standard error of the linear regression estimate over each rolling window.

    ``se = sqrt( sum((y - yhat)^2) / (period - 2) ) / sqrt(period)``

    where ``yhat`` values come from the OLS line fitted over ``x = 0..period-1``.
    Reuses the shared ``_ols`` helper.  Returns ``None`` during warm-up.
    """
    n = len(values)
    out: list[float | None] = [None] * n
    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        b, a = _ols(window)
        residuals_sq = sum((window[j] - (a + b * j)) ** 2 for j in range(period))
        if period > 2:
            mse = residuals_sq / (period - 2)
            out[i] = math.sqrt(mse) / math.sqrt(period)
        else:
            out[i] = 0.0
    return out


@indicator(
    category="statistics",
    inputs=["close"],
    params=[
        Param("period", "int", 20, 2, 200, 1),
        Param("mult", "float", 2.0, 0.1, 10.0, 0.1),
    ],
    outputs=["upper", "mid", "lower"],
)
def std_error_bands(values, period: int = 20, mult: float = 2.0):
    """Standard error bands around the linear regression line.

    ``mid = linearreg(period)``
    ``upper = mid + mult * std_error(period)``
    ``lower = mid - mult * std_error(period)``

    Returns ``(upper, mid, lower)``, all aligned to ``values``.
    """
    lr = linearreg(values, period)
    se = std_error(values, period)
    n = len(values)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    mid: list[float | None] = list(lr)  # copy
    for i in range(n):
        if lr[i] is not None and se[i] is not None:
            upper[i] = lr[i] + mult * se[i]
            lower[i] = lr[i] - mult * se[i]
    return upper, mid, lower


@indicator(
    category="statistics",
    inputs=["close"],
    params=[Param("period", "int", 14, 2, 200, 1)],
    outputs=["rci"],
)
def rank_correlation(values, period: int = 14):
    """Spearman rank-correlation of price vs the time index, scaled to [-100, 100].

    Formula: ``100 * (1 - 6 * Σd² / (period * (period² - 1)))``
    where ``d[j] = rank(price[j]) - rank(time_index[j])`` over the window.

    The time index is ``[0, 1, ..., period-1]`` whose ranks are always
    ``[1, 2, ..., period]``.  The price ranks are computed by
    ``argsort(argsort(window))`` (1-based, ascending).

    Output name: ``rci`` (Rank Correlation Index).
    """
    n = len(values)
    out: list[float | None] = [None] * n
    p = period
    p2 = p * p

    for i in range(p - 1, n):
        window = values[i - p + 1 : i + 1]
        # Rank prices (1-based ascending): sort by value to get rank
        indexed = sorted(range(p), key=lambda j: window[j])
        price_rank = [0] * p
        for rank, j in enumerate(indexed, start=1):
            price_rank[j] = rank
        # Time-index rank is simply [1, 2, ..., p] (time is already ordered)
        sum_d2 = sum((price_rank[j] - (j + 1)) ** 2 for j in range(p))
        denom = p * (p2 - 1)
        if denom != 0:
            out[i] = 100.0 * (1.0 - 6.0 * sum_d2 / denom)
        else:
            out[i] = 0.0
    return out


@indicator(
    category="statistics",
    inputs=["close", "benchmark"],
    params=[Param("period", "int", 30, 2, 200, 1)],
    outputs=["correl_log"],
)
def correl_log(closes, benchmarks, period: int = 30):
    """Rolling Pearson correlation of LOG RETURNS of ``close`` vs ``benchmark``.

    Log return at bar ``i``: ``ln(close[i] / close[i-1])``.
    Requires at least ``period + 1`` bars (one extra for the first log return).
    Returns ``None`` during warm-up and whenever either series has
    non-positive values (log undefined).
    """
    n = len(closes)
    # Compute log-return series (index 0 is None — no prior bar)
    lr_a: list[float | None] = [None] * n
    lr_b: list[float | None] = [None] * n
    for i in range(1, n):
        if closes[i] > 0 and closes[i - 1] > 0:
            lr_a[i] = math.log(closes[i] / closes[i - 1])
        if benchmarks[i] > 0 and benchmarks[i - 1] > 0:
            lr_b[i] = math.log(benchmarks[i] / benchmarks[i - 1])

    # Rolling Pearson over the log-return pairs using a sliding buffer
    out: list[float | None] = [None] * n
    buf_a: list[float] = []
    buf_b: list[float] = []
    # Track original indices for placement
    for i in range(1, n):
        if lr_a[i] is not None and lr_b[i] is not None:
            buf_a.append(lr_a[i])
            buf_b.append(lr_b[i])
        else:
            buf_a.clear()
            buf_b.clear()
            continue
        if len(buf_a) > period:
            buf_a.pop(0)
            buf_b.pop(0)
        if len(buf_a) == period:
            p = period
            sa = sum(buf_a)
            sb = sum(buf_b)
            sa2 = sum(x * x for x in buf_a)
            sb2 = sum(x * x for x in buf_b)
            sab = sum(buf_a[j] * buf_b[j] for j in range(p))
            num = p * sab - sa * sb
            denom = math.sqrt(
                max((p * sa2 - sa * sa) * (p * sb2 - sb * sb), 0.0)
            )
            if denom != 0.0:
                out[i] = max(-1.0, min(1.0, num / denom))
    return out
