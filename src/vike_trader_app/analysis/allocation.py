"""Composable allocation layer (bt-style): Select -> Weigh -> (Rebalance).

Pure functions that produce a ``{symbol: weight}`` dict you feed straight into
``PortfolioStrategy.rebalance(weights)``. Selection picks the basket; weighting sets
the sizes (equal / inverse-vol / risk-parity-ERC / min-variance). Weights always sum
to 1 over the selected symbols.
"""

import numpy as np


# --- selection ---
def select_top_n(scores: dict, n: int) -> list:
    """Return the ``n`` symbols with the highest score (descending)."""
    return [s for s, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:n]]


def select_where(mask: dict) -> list:
    """Return the symbols whose boolean condition is True."""
    return [s for s, ok in mask.items() if ok]


# --- weighting ---
def weigh_equally(symbols) -> dict:
    """Equal weight across ``symbols``."""
    symbols = list(symbols)
    w = 1.0 / len(symbols) if symbols else 0.0
    return {s: w for s in symbols}


def weigh_inverse_vol(vol_by_symbol: dict) -> dict:
    """Weight inversely proportional to volatility (lower vol -> larger weight)."""
    inv = {s: (1.0 / v if v > 0 else 0.0) for s, v in vol_by_symbol.items()}
    total = sum(inv.values()) or 1.0
    return {s: x / total for s, x in inv.items()}


def weigh_min_variance(symbols, cov) -> dict:
    """Global minimum-variance weights: ``w = inv(cov)1 / (1' inv(cov) 1)`` (long/short ok)."""
    symbols = list(symbols)
    sigma = np.asarray(cov, dtype=float)
    ones = np.ones(len(symbols))
    inv = np.linalg.pinv(sigma)
    raw = inv @ ones
    w = raw / (ones @ raw)
    return dict(zip(symbols, w.tolist(), strict=True))


def weigh_risk_parity(symbols, cov, iters: int = 2000, tol: float = 1e-10) -> dict:
    """Equal-risk-contribution (ERC) weights via fixed-point iteration (long-only)."""
    symbols = list(symbols)
    sigma = np.asarray(cov, dtype=float)
    n = len(symbols)
    w = np.ones(n) / n
    for _ in range(iters):
        mrc = sigma @ w               # marginal risk contribution
        rc = w * mrc                  # risk contribution per asset
        target = rc.sum() / n         # each asset should contribute equally
        # sqrt-damped multiplicative update (Spinu/Maillard) — converges; the
        # undamped w*target/rc oscillates for uncorrelated assets.
        new = w * np.sqrt(target / (rc + 1e-15))
        new = np.clip(new, 1e-12, None)
        new /= new.sum()
        if np.max(np.abs(new - w)) < tol:
            w = new
            break
        w = new
    return dict(zip(symbols, w.tolist(), strict=True))


# --- helpers to build vol/cov from per-symbol return series ---
def cov_matrix(returns_by_symbol: dict, symbols):
    """Covariance matrix (numpy) over the given symbol order from equal-length returns."""
    symbols = list(symbols)
    mat = np.array([returns_by_symbol[s] for s in symbols], dtype=float)
    return np.cov(mat)


def vol_by_symbol(returns_by_symbol: dict) -> dict:
    """Per-symbol standard deviation of returns."""
    return {s: float(np.std(r, ddof=1)) for s, r in returns_by_symbol.items()}
