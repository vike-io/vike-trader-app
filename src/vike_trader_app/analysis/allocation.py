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


# --- portfolio constraints ---

def clamp_weights(weights: dict, max_weight: float, min_weight: float = 0.0) -> dict:
    """Cap each weight at *max_weight*, redistributing excess pro-rata to uncapped names.

    Iterates to a fixed point so no name exceeds ``max_weight`` after redistribution.
    Preserves ``sum(weights)`` when feasible.  When ``max_weight * len(weights)`` is
    less than ``sum(weights)`` the redistribution is infeasible and every name is
    clamped to ``max_weight`` (sum is reduced in that degenerate case).
    """
    if not weights:
        return {}
    w = dict(weights)
    max_iters = len(w) + 10  # finite bound; converges in at most n passes

    for _ in range(max_iters):
        capped = {s: v for s, v in w.items() if v >= max_weight}
        free = {s: v for s, v in w.items() if v < max_weight}

        if not capped:
            break  # all names within cap — stable

        # Check feasibility: can the free names absorb the excess?
        excess = sum(v - max_weight for v in capped.values())
        free_headroom = sum(max_weight - v for v in free.values()) if free else 0.0

        if not free or free_headroom <= 0.0:
            # Infeasible: pin everyone at max_weight
            return {s: max_weight for s in w}

        # Pin capped names; redistribute excess to free names pro-rata by current weight
        free_total = sum(free.values()) or 1.0
        new_w = {s: max_weight for s in capped}
        for s, v in free.items():
            new_w[s] = v + excess * (v / free_total)

        w = new_w

    return w


def cap_group_exposure(weights: dict, groups: dict, max_per_group: float,
                       max_names_per_group: int | None = None) -> dict:
    """Scale down each group so its total weight <= *max_per_group*.

    Optionally keep only the top ``max_names_per_group`` by weight within a group
    (remaining names are zeroed and then the survivors are scaled).
    ``groups`` maps symbol -> group label; symbols absent from ``groups`` are left alone.
    """
    if not weights:
        return {}

    # Build group membership
    group_members: dict[str, list] = {}
    for s, g in groups.items():
        if s in weights:
            group_members.setdefault(g, []).append(s)

    result = dict(weights)

    for g, members in group_members.items():
        # Optionally trim to top-N within the group
        if max_names_per_group is not None:
            sorted_members = sorted(members, key=lambda s: result[s], reverse=True)
            for s in sorted_members[max_names_per_group:]:
                result[s] = 0.0
            members = sorted_members[:max_names_per_group]

        group_total = sum(result[s] for s in members)
        if group_total > max_per_group and group_total > 0.0:
            scale = max_per_group / group_total
            for s in members:
                result[s] = result[s] * scale

    return result


def apply_cash_reserve(weights: dict, target_invested: float = 1.0) -> dict:
    """Scale all weights down so ``sum(weights) == target_invested``.

    If the current sum is already <= ``target_invested`` the weights are returned
    unchanged (never scaled up).
    """
    if not weights:
        return {}
    total = sum(weights.values())
    if total <= target_invested:
        return dict(weights)
    scale = target_invested / total
    return {s: v * scale for s, v in weights.items()}


def apply_turnover_band(target: dict, current: dict, band: float) -> dict:
    """No-trade band: keep ``current[s]`` when ``|target[s] - current[s]| < band``.

    Symbols absent from one dict are treated as weight 0 in that dict.
    """
    all_symbols = set(target) | set(current)
    result = {}
    for s in all_symbols:
        t = target.get(s, 0.0)
        c = current.get(s, 0.0)
        result[s] = c if abs(t - c) < band else t
    return result


def select_decorrelated(candidates: list, cov, max_corr: float) -> list:
    """Greedily keep candidates in order, dropping those too correlated with kept names.

    ``cov`` may be:
    - a numpy 2D array (from :func:`cov_matrix`) — then ``candidates`` must be the
      same symbols passed to ``cov_matrix`` and we use their index positions, OR
    - a nested dict ``{a: {b: covariance}}`` / flat ``{(a, b): covariance}``.

    Correlation is derived as ``cov(a,b) / sqrt(cov(a,a) * cov(b,b))``.
    A candidate with missing covariance data is always kept (conservative).
    """
    kept: list = []

    def _get_cov(a, b):
        """Return covariance(a, b) or None if unavailable."""
        # Numpy array path
        if hasattr(cov, '__array__') or (hasattr(cov, 'shape') and hasattr(cov, '__getitem__')):
            try:
                ia = candidates.index(a)
                ib = candidates.index(b)
                return float(cov[ia][ib])
            except (ValueError, IndexError):
                return None
        # Nested dict path: {a: {b: val}}
        if isinstance(cov, dict):
            top = cov.get(a) or cov.get(b)
            if isinstance(top, dict):
                val = (cov.get(a) or {}).get(b)
                if val is None:
                    val = (cov.get(b) or {}).get(a)
                return val
            # Flat tuple-keyed dict: {(a, b): val}
            val = cov.get((a, b))
            if val is None:
                val = cov.get((b, a))
            return val
        return None

    def _corr(a, b):
        cov_ab = _get_cov(a, b)
        cov_aa = _get_cov(a, a)
        cov_bb = _get_cov(b, b)
        if any(v is None for v in (cov_ab, cov_aa, cov_bb)):
            return None
        denom = (cov_aa * cov_bb) ** 0.5
        if denom == 0.0:
            return None
        return cov_ab / denom

    for candidate in candidates:
        too_correlated = False
        for k in kept:
            r = _corr(candidate, k)
            if r is not None and abs(r) > max_corr:
                too_correlated = True
                break
        if not too_correlated:
            kept.append(candidate)

    return kept
