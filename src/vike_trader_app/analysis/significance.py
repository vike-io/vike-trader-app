"""Bootstrap confidence intervals + a rule-significance p-value.

Complements the deflated-Sharpe / PBO suite: where those correct for *multiple
testing*, these quantify the noise in a *single* strategy's metric and test whether
its edge is distinguishable from luck.

- ``bootstrap_ci``: percentile bootstrap CI for any statistic of a sample (resample
  with replacement, recompute, take the alpha/2 and 1-alpha/2 percentiles).
- ``rule_significance``: sign-flip permutation test of H0 "mean return == 0"; returns
  a one-sided p-value that the mean is positive.

Deterministic given ``seed`` (stdlib ``random`` only).
"""

import random


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _percentile(sorted_vals, q: float):
    if not sorted_vals:
        return 0.0
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 < len(sorted_vals):
        return sorted_vals[lo] * (1 - frac) + sorted_vals[lo + 1] * frac
    return sorted_vals[lo]


def bootstrap_ci(values, stat_fn=_mean, n_boot: int = 1000, alpha: float = 0.05, seed: int = 0):
    """Return ``(low, point, high)`` — a percentile bootstrap CI for ``stat_fn(values)``."""
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    n = len(values)
    point = stat_fn(values)
    stats = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        stats.append(stat_fn(sample))
    stats.sort()
    return _percentile(stats, alpha / 2), point, _percentile(stats, 1 - alpha / 2)


def rule_significance(returns, n_perm: int = 1000, seed: int = 0) -> float:
    """One-sided p-value that mean(``returns``) > 0 under a sign-flip null.

    Each permutation randomly flips the sign of every return (a symmetric null that
    preserves magnitudes). p = (#{null mean >= observed mean} + 1) / (n_perm + 1).
    """
    if not returns:
        return 1.0
    rng = random.Random(seed)
    observed = _mean(returns)
    ge = 0
    for _ in range(n_perm):
        flipped = [r if rng.random() < 0.5 else -r for r in returns]
        if _mean(flipped) >= observed:
            ge += 1
    return (ge + 1) / (n_perm + 1)
