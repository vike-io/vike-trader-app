"""Black–Scholes greeks computed locally from implied volatility.

Both feeds give IV but not full greeks, so we derive Δ/Γ/Θ/V uniformly. European
exercise, continuous-compounding, no dividends; r defaults to 0.0 in v1 (the spec
makes it configurable in a later phase). Θ is per calendar day, V per 1 vol-point;
the year basis is 365 calendar days (matching the per-day Θ denominator).
"""

from __future__ import annotations

import math
from dataclasses import replace

from .model import OptionQuote, _expiry_ms

_RISK_FREE = 0.0  # v1 constant; later: configurable
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
