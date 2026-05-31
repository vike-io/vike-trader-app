"""Pairs / spread indicators — 2-token indicators for ratio/spread analysis.

All functions accept two aligned series: ``close`` (asset) and ``benchmark``
(second token).  Outputs are input-aligned ``list[float | None]`` with ``None``
wherever the computation is undefined (e.g. zero denominator, log of
non-positive value, warm-up period).

The second series is declared as ``benchmark`` so the catalog smoke test
(which provides a ``benchmark`` synthetic series) feeds it automatically.
"""

import math

from .base import Param, indicator


@indicator(
    category="pairs",
    inputs=["close", "benchmark"],
    params=[],
    outputs=["ratio"],
)
def ratio(closes, benchmarks):
    """Price ratio of two series: ``close[i] / benchmark[i]``.

    Returns ``None`` wherever ``benchmark[i] == 0`` (division undefined).
    No warm-up period — each bar is immediately defined (unless b==0).
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    for i in range(n):
        b = benchmarks[i]
        if b != 0.0:
            out[i] = closes[i] / b
    return out


@indicator(
    category="pairs",
    inputs=["close", "benchmark"],
    params=[Param("log", "int", 0, 0, 1, 1)],
    outputs=["spread"],
)
def spread(closes, benchmarks, log: int = 0):
    """Arithmetic or log spread between two series.

    ``log=0`` (default): ``spread[i] = close[i] - benchmark[i]``
    ``log=1``:           ``spread[i] = ln(close[i]) - ln(benchmark[i])``

    In log mode, returns ``None`` wherever either value is non-positive
    (log is undefined for zero or negative prices).  In arithmetic mode,
    all bars are always defined.
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    if log == 0:
        for i in range(n):
            out[i] = closes[i] - benchmarks[i]
    else:
        for i in range(n):
            a, b = closes[i], benchmarks[i]
            if a > 0 and b > 0:
                out[i] = math.log(a) - math.log(b)
    return out


@indicator(
    category="pairs",
    inputs=["close", "benchmark"],
    params=[Param("period", "int", 20, 2, 200, 1)],
    outputs=["zscore"],
)
def spread_zscore(closes, benchmarks, period: int = 20):
    """Rolling z-score of the arithmetic spread ``(close - benchmark)``.

    Canonical pairs-trading mean-reversion signal.  Formula:
        ``zscore[i] = (s[i] - mean(s, period)) / popstddev(s, period)``
    where ``s[i] = close[i] - benchmark[i]``.

    Returns ``None`` during the warm-up period (first ``period-1`` bars) and
    wherever the rolling standard deviation is zero (constant spread).
    """
    n = len(closes)
    out: list[float | None] = [None] * n
    # Compute arithmetic spread series
    s: list[float] = [closes[i] - benchmarks[i] for i in range(n)]
    # Rolling z-score using running sums for efficiency
    run_sum  = 0.0
    run_sum2 = 0.0
    for i in range(n):
        run_sum  += s[i]
        run_sum2 += s[i] * s[i]
        if i >= period:
            run_sum  -= s[i - period]
            run_sum2 -= s[i - period] * s[i - period]
        if i >= period - 1:
            mean   = run_sum / period
            var_v  = run_sum2 / period - mean * mean
            sd     = math.sqrt(max(var_v, 0.0))
            if sd != 0.0:
                out[i] = (s[i] - mean) / sd
            # else: leave as None (constant spread → undefined z-score)
    return out
