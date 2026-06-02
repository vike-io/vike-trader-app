"""Black–Scholes greeks computed locally from implied volatility.

Both feeds give IV but not full greeks, so we derive Δ/Γ/Θ/V uniformly. European
exercise, continuous-compounding, no dividends; r defaults to 0.0 in v1 (the spec
makes it configurable in a later phase). Θ is per calendar day, V per 1 vol-point;
the year basis is 365 calendar days (matching the per-day Θ denominator).
"""

from __future__ import annotations

import math
import os
from dataclasses import replace

from .model import OptionQuote, _expiry_ms


def _env_risk_free() -> float:
    """Risk-free rate from `options_risk_free` (e.g. 0.04), else 0.0."""
    try:
        return float(os.environ.get("options_risk_free") or 0.0)
    except ValueError:
        return 0.0


_RISK_FREE = _env_risk_free()   # resolved at import (after the app's load_dotenv())
_MS_PER_YEAR = 365.0 * 86_400 * 1000


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    S: float | None, K: float | None, t: float | None, sigma: float | None,
    kind: str, r: float = _RISK_FREE,
) -> tuple[float, float, float, float] | None:
    """Return (delta, gamma, theta_per_day, vega_per_point) or None if inputs invalid."""
    if S is None or K is None or t is None or sigma is None:
        return None
    if S <= 0 or K <= 0 or t <= 0 or sigma <= 0:
        return None
    sqrt_t = math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    pdf_d1 = _norm_pdf(d1)
    if kind == "C":
        delta = _norm_cdf(d1)
        theta = -(S * pdf_d1 * sigma) / (2.0 * sqrt_t) - r * K * math.exp(-r * t) * _norm_cdf(d2)
    elif kind == "P":
        delta = _norm_cdf(d1) - 1.0
        theta = -(S * pdf_d1 * sigma) / (2.0 * sqrt_t) + r * K * math.exp(-r * t) * _norm_cdf(-d2)
    else:  # bad kind is a programmer error (model types it Literal["C","P"]), not bad data
        raise ValueError(f"kind must be 'C' or 'P', got {kind!r}")
    gamma = pdf_d1 / (S * sigma * sqrt_t)
    vega = S * pdf_d1 * sqrt_t
    return (delta, gamma, theta / 365.0, vega / 100.0)


def black_scholes_price(
    S: float | None, K: float | None, t: float | None, sigma: float | None,
    kind: str, r: float = _RISK_FREE,
) -> float | None:
    """Black–Scholes option price, or None if inputs invalid."""
    if S is None or K is None or t is None or sigma is None:
        return None
    if S <= 0 or K <= 0 or t <= 0 or sigma <= 0:
        return None
    sqrt_t = math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc = math.exp(-r * t)
    if kind == "C":
        return S * _norm_cdf(d1) - K * disc * _norm_cdf(d2)
    if kind == "P":
        return K * disc * _norm_cdf(-d2) - S * _norm_cdf(-d1)
    raise ValueError(f"kind must be 'C' or 'P', got {kind!r}")


def implied_vol(
    price: float | None, S: float | None, K: float | None, t: float | None,
    kind: str, r: float = _RISK_FREE,
) -> float | None:
    """Invert Black–Scholes for sigma via bisection (sigma in [1e-4, 5.0]).

    Returns None when price/inputs are invalid or the price is outside the no-arbitrage
    band (e.g. below intrinsic) — i.e. not solvable.
    """
    if price is None or S is None or K is None or t is None:
        return None
    if price <= 0 or S <= 0 or K <= 0 or t <= 0:
        return None
    lo, hi = 1e-4, 5.0
    p_lo = black_scholes_price(S, K, t, lo, kind, r)
    p_hi = black_scholes_price(S, K, t, hi, kind, r)
    if p_lo is None or p_hi is None or not (p_lo <= price <= p_hi):
        return None
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        pm = black_scholes_price(S, K, t, mid, kind, r)
        if pm is None:
            return None
        if abs(pm - price) < 1e-6:
            return mid
        if pm < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def years_to_expiry(expiry_iso: str, now_ms: int) -> float:
    """Time to expiry in years (clamped to >= 0), expiry assumed ~08:00 UTC."""
    return max((_expiry_ms(expiry_iso) - now_ms) / _MS_PER_YEAR, 0.0)


def enrich_quote(q: OptionQuote, S: float | None, t: float) -> OptionQuote:
    """Return a copy of `q` with greeks filled from its IV.

    Unchanged if greeks aren't computable (no IV, or t<=0 for an expired option).
    """
    g = black_scholes_greeks(S, q.strike, t, q.iv, q.type)
    if g is None:
        return q
    delta, gamma, theta, vega = g
    return replace(q, delta=delta, gamma=gamma, theta=theta, vega=vega)
