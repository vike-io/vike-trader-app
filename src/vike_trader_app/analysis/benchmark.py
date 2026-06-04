"""Benchmark-comparison analytics: alpha, beta, correlation, capture ratios, etc.

All functions operate on plain Python lists of equal-length equity curves.
Raise ``ValueError`` on length mismatch; return ``0.0`` (or ``nan``-safe defaults)
for degenerate inputs (empty, zero-variance, etc.).
"""

import math


def _returns(equity_curve: list[float]) -> list[float]:
    """Simple per-step returns: (eq[i] / eq[i-1]) - 1, skipping zero denominators."""
    out = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev != 0:
            out.append(equity_curve[i] / prev - 1.0)
        else:
            out.append(0.0)
    return out


def _check_lengths(a: list[float], b: list[float]) -> None:
    if len(a) != len(b):
        raise ValueError(
            f"equity curves must be the same length (got {len(a)} vs {len(b)})"
        )


def _variance(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mu = sum(xs) / n
    return sum((x - mu) ** 2 for x in xs) / (n - 1)


def _covariance(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    return sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)


def _cagr(equity_curve: list[float], periods_per_year: float) -> float:
    """Annualised growth rate; 0.0 for a flat/short/non-positive curve."""
    n = len(equity_curve) - 1  # number of steps
    if n < 1 or equity_curve[0] <= 0:
        return 0.0
    growth = equity_curve[-1] / equity_curve[0]
    if growth <= 0:
        return 0.0
    exponent = periods_per_year / n
    if exponent > 1000:
        return 0.0
    try:
        return growth ** exponent - 1.0
    except OverflowError:
        return 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def returns(equity_curve: list[float]) -> list[float]:
    """Per-step simple returns (len = len(equity_curve) - 1)."""
    return _returns(equity_curve)


def beta(strat_eq: list[float], bench_eq: list[float]) -> float:
    """cov(rs, rb) / var(rb).  Returns 0.0 when benchmark has zero variance."""
    _check_lengths(strat_eq, bench_eq)
    rs = _returns(strat_eq)
    rb = _returns(bench_eq)
    vb = _variance(rb)
    if vb == 0:
        return 0.0
    return _covariance(rs, rb) / vb


def alpha(
    strat_eq: list[float],
    bench_eq: list[float],
    periods_per_year: float,
    rf: float = 0.0,
) -> float:
    """Annualised Jensen's alpha: CAGR_s - [rf + beta * (CAGR_b - rf)]."""
    _check_lengths(strat_eq, bench_eq)
    b = beta(strat_eq, bench_eq)
    cagr_s = _cagr(strat_eq, periods_per_year)
    cagr_b = _cagr(bench_eq, periods_per_year)
    return cagr_s - (rf + b * (cagr_b - rf))


def correlation(strat_eq: list[float], bench_eq: list[float]) -> float:
    """Pearson correlation of per-step returns.  0.0 when either series has zero variance."""
    _check_lengths(strat_eq, bench_eq)
    rs = _returns(strat_eq)
    rb = _returns(bench_eq)
    n = len(rs)
    if n < 2:
        return 0.0
    vs = _variance(rs)
    vb = _variance(rb)
    if vs <= 0 or vb <= 0:
        return 0.0
    cov = _covariance(rs, rb)
    return cov / math.sqrt(vs * vb)


def r_squared(strat_eq: list[float], bench_eq: list[float]) -> float:
    """Coefficient of determination: correlation ** 2."""
    return correlation(strat_eq, bench_eq) ** 2


def tracking_error(
    strat_eq: list[float], bench_eq: list[float], periods_per_year: float
) -> float:
    """Annualised std-dev of the return differential (rs - rb)."""
    _check_lengths(strat_eq, bench_eq)
    rs = _returns(strat_eq)
    rb = _returns(bench_eq)
    if len(rs) < 2:
        return 0.0
    diffs = [rs[i] - rb[i] for i in range(len(rs))]
    var = _variance(diffs)
    return math.sqrt(var * periods_per_year)


def information_ratio(
    strat_eq: list[float], bench_eq: list[float], periods_per_year: float
) -> float:
    """Annualised mean(rs - rb) / std(rs - rb).  0.0 when tracking error is zero."""
    _check_lengths(strat_eq, bench_eq)
    rs = _returns(strat_eq)
    rb = _returns(bench_eq)
    if len(rs) < 2:
        return 0.0
    diffs = [rs[i] - rb[i] for i in range(len(rs))]
    mean_diff = sum(diffs) / len(diffs)
    var = _variance(diffs)
    te = math.sqrt(var)
    if te == 0:
        return 0.0
    return (mean_diff / te) * math.sqrt(periods_per_year)


def up_capture(strat_eq: list[float], bench_eq: list[float]) -> float:
    """Upside capture ratio: mean(rs | rb > 0) / mean(rb | rb > 0).

    Returns 0.0 when the benchmark has no positive-return bars.
    """
    _check_lengths(strat_eq, bench_eq)
    rs = _returns(strat_eq)
    rb = _returns(bench_eq)
    up_rs = [rs[i] for i in range(len(rb)) if rb[i] > 0]
    up_rb = [rb[i] for i in range(len(rb)) if rb[i] > 0]
    if not up_rb:
        return 0.0
    mean_rb = sum(up_rb) / len(up_rb)
    if mean_rb == 0:
        return 0.0
    mean_rs = sum(up_rs) / len(up_rs)
    return mean_rs / mean_rb


def down_capture(strat_eq: list[float], bench_eq: list[float]) -> float:
    """Downside capture ratio: mean(rs | rb < 0) / mean(rb | rb < 0).

    Returns 0.0 when the benchmark has no negative-return bars.
    """
    _check_lengths(strat_eq, bench_eq)
    rs = _returns(strat_eq)
    rb = _returns(bench_eq)
    dn_rs = [rs[i] for i in range(len(rb)) if rb[i] < 0]
    dn_rb = [rb[i] for i in range(len(rb)) if rb[i] < 0]
    if not dn_rb:
        return 0.0
    mean_rb = sum(dn_rb) / len(dn_rb)
    if mean_rb == 0:
        return 0.0
    mean_rs = sum(dn_rs) / len(dn_rs)
    return mean_rs / mean_rb


def benchmark_stats(
    strat_eq: list[float],
    bench_eq: list[float],
    periods_per_year: float,
    rf: float = 0.0,
) -> dict:
    """Bundle all benchmark metrics into a single dict."""
    _check_lengths(strat_eq, bench_eq)
    return {
        "beta": beta(strat_eq, bench_eq),
        "alpha": alpha(strat_eq, bench_eq, periods_per_year, rf),
        "correlation": correlation(strat_eq, bench_eq),
        "r_squared": r_squared(strat_eq, bench_eq),
        "tracking_error": tracking_error(strat_eq, bench_eq, periods_per_year),
        "information_ratio": information_ratio(strat_eq, bench_eq, periods_per_year),
        "up_capture": up_capture(strat_eq, bench_eq),
        "down_capture": down_capture(strat_eq, bench_eq),
    }
