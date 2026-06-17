"""Anti-overfitting statistics (López de Prado, *Advances in Financial ML*).

- ``probabilistic_sharpe_ratio`` (PSR) and ``deflated_sharpe_ratio`` (DSR): is the
  observed Sharpe significant once you account for track-record length, return
  non-normality, and the number of strategy configurations tried?
- ``pbo_cscv``: Probability of Backtest Overfitting via Combinatorially Symmetric
  Cross-Validation — how often the in-sample best is below the out-of-sample median.
- ``overfit_verdict``: a plain-language Low/Medium/High risk label.
"""

import math
from dataclasses import dataclass
from itertools import combinations
from statistics import NormalDist, variance

_N01 = NormalDist()
_EULER = 0.5772156649015329


def probabilistic_sharpe_ratio(
    sr: float, n: int, sr_star: float = 0.0, skew: float = 0.0, kurt: float = 3.0
) -> float:
    """P(true Sharpe > ``sr_star``) given observed (per-observation) Sharpe ``sr``.

    ``n`` = number of return observations; ``kurt`` is non-excess (normal = 3).
    """
    if n < 2:
        return 0.0
    denom = math.sqrt(max(1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr, 1e-12))
    return _N01.cdf((sr - sr_star) * math.sqrt(n - 1) / denom)


def expected_max_sharpe(var_trials: float, n_trials: int) -> float:
    """Expected maximum Sharpe across ``n_trials`` independent trials (the DSR benchmark)."""
    if n_trials < 2 or var_trials <= 0:
        return 0.0
    z1 = _N01.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _N01.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_trials) * ((1.0 - _EULER) * z1 + _EULER * z2)


def deflated_sharpe_ratio(
    observed_sr: float, trial_sharpes, n_obs: int, skew: float = 0.0, kurt: float = 3.0
) -> float:
    """Deflated Sharpe: PSR benchmarked against the expected max Sharpe of the trials."""
    n_trials = len(trial_sharpes)
    var_trials = variance(trial_sharpes) if n_trials > 1 else 0.0
    sr_star = expected_max_sharpe(var_trials, n_trials)
    return probabilistic_sharpe_ratio(observed_sr, n_obs, sr_star, skew, kurt)


def _means(matrix, rows):
    n_cols = len(matrix[0])
    return [sum(matrix[t][j] for t in rows) / len(rows) for j in range(n_cols)]


def pbo_cscv(matrix, n_splits: int) -> float:
    """Probability of Backtest Overfitting via CSCV.

    ``matrix`` is T observations x N trials of per-observation performance. Rows are
    split into ``n_splits`` (even) groups; for every way to pick half as in-sample,
    we check whether the in-sample best trial lands below the out-of-sample median.
    """
    if n_splits % 2 != 0:
        raise ValueError("n_splits must be even for CSCV")
    t = len(matrix)
    n_cols = len(matrix[0])
    # A NaN/inf anywhere makes the IS-best / OOS-rank comparisons meaningless (NaN compares False ->
    # garbage rank, and a rank of 0 would make math.log(0) raise). PBO is then UNCOMPUTABLE: return
    # NaN (which overfit_verdict renders as "not assessed"), NOT 0.0 — 0.0 reads as a reassuring
    # "no overfit" and the raw call sites (report.py, ai/services.py) used to crash on this input.
    if any(not math.isfinite(v) for row in matrix for v in row):
        return float("nan")
    bounds = [(g * t // n_splits, (g + 1) * t // n_splits) for g in range(n_splits)]
    groups = [list(range(a, b)) for a, b in bounds]

    logits = []
    for combo in combinations(range(n_splits), n_splits // 2):
        is_rows = [i for g in combo for i in groups[g]]
        oos_rows = [i for g in range(n_splits) if g not in combo for i in groups[g]]
        if not is_rows or not oos_rows:
            continue
        is_perf = _means(matrix, is_rows)
        oos_perf = _means(matrix, oos_rows)
        best = max(range(n_cols), key=lambda j: is_perf[j])
        rank = sum(1 for j in range(n_cols) if oos_perf[j] <= oos_perf[best])
        omega = rank / (n_cols + 1)
        if not 0.0 < omega < 1.0:   # degenerate split -> skip (logit undefined at 0/1)
            continue
        logits.append(math.log(omega / (1.0 - omega)))

    if not logits:
        return float("nan")
    return sum(1 for lam in logits if lam <= 0.0) / len(logits)


@dataclass
class Verdict:
    """Plain-language overfitting risk."""

    level: str  # "Low" | "Medium" | "High"
    reasons: list[str]


def overfit_verdict(pbo: float, deflated_sr: float, wf_consistency: float | None = None) -> Verdict:
    """Combine PBO, deflated Sharpe, and (optional) walk-forward consistency into a label."""
    points = 0
    reasons: list[str] = []

    if math.isnan(pbo):
        # PBO couldn't be computed (degenerate / non-finite returns matrix). Surface that honestly
        # rather than letting NaN comparisons fall through as a silent "no overfit".
        reasons.append("PBO not assessed (degenerate or non-finite returns matrix).")
    elif pbo > 0.5:
        points += 2
        reasons.append(
            f"PBO {pbo:.0%}: the selected configuration is more likely than not overfit."
        )
    elif pbo > 0.2:
        points += 1
        reasons.append(f"PBO {pbo:.0%}: moderate chance the result is curve-fit.")

    if deflated_sr < 0.5:
        points += 2
        reasons.append(
            f"Deflated Sharpe {deflated_sr:.0%}: edge not significant after the number of trials."
        )
    elif deflated_sr < 0.9:
        points += 1
        reasons.append(f"Deflated Sharpe {deflated_sr:.0%}: significance is borderline.")

    if wf_consistency is not None and wf_consistency < 0.5:
        points += 1
        reasons.append(
            f"Only {wf_consistency:.0%} of walk-forward windows were profitable out-of-sample."
        )

    level = "High" if points >= 3 else "Medium" if points >= 1 else "Low"
    if not reasons:
        reasons = ["No major overfitting flags."]
    return Verdict(level=level, reasons=reasons)


def _pearson(a, b) -> float:
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def effective_n_trials(return_series) -> float:
    """Correlation-aware effective trial count: N / (1 + (N-1)*max(mean_corr, 0)), clamped [1, N].

    Perfectly-correlated trials collapse to ~1; uncorrelated/anti-correlated stay ~N. 0.0 if empty.
    """
    n = len(return_series)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    corrs = [_pearson(return_series[i], return_series[j]) for i in range(n) for j in range(i + 1, n)]
    avg = sum(corrs) / len(corrs) if corrs else 0.0
    r = max(avg, 0.0)
    eff = n / (1.0 + (n - 1) * r)
    return min(max(eff, 1.0), float(n))


def deflated_sharpe_with_effective_n(
    observed_sr: float, trial_sharpes, trial_return_series, n_obs: int,
    skew: float = 0.0, kurt: float = 3.0,
) -> float:
    """DSR benchmarked against expected-max-Sharpe computed with the EFFECTIVE (correlation-corrected) trial count."""
    eff = max(int(round(effective_n_trials(trial_return_series))), 1)
    var_trials = variance(trial_sharpes) if len(trial_sharpes) > 1 else 0.0
    sr_star = expected_max_sharpe(var_trials, eff)
    return probabilistic_sharpe_ratio(observed_sr, n_obs, sr_star, skew, kurt)
