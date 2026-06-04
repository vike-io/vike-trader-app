"""Parameter-stability scoring for optimization results.

WealthLab's "Parameter Stability" graph shows how performance varies across the
parameter neighbourhood — a broad plateau is robust; a lonely spike is fragile.
This module quantifies that into a single ``[0, 1]`` score and a three-way label.

Formula
-------
Given N trials with scores s_0 >= s_1 >= ... >= s_{N-1} (best first):

1. Select the top ``top_frac`` fraction of trials (at least 1).
2. Compute the **plateau ratio**: mean(top scores) / best_score.
   - If the best sits on a broad flat top (all neighbors ≈ best), the ratio → 1.
   - If the best is a lonely spike (neighbors << best), the ratio → 0.
3. Clamp the result to [0, 1] so that negative scores and numerical noise never
   produce out-of-range values.

Edge cases
----------
- Empty or single trial     → returns 1.0 (trivially stable; no evidence of spikiness).
- best_score == 0           → returns 1.0 (cannot normalise; treat as non-spike).
- best_score < 0            → all scores are ≤ 0; we negate and re-scale so that a
  cluster of "equally-bad" scores still reads as a plateau.

Labels (``stability_label``)
-----------------------------
- ``'plateau'``: stability >= 0.8  — robust neighbourhood; safe to trade.
- ``'ridge'``:   stability >= 0.5  — moderate sensitivity; worth checking adjacent params.
- ``'spike'``:   stability <  0.5  — fragile optimum; likely overfit to this exact combo.
"""

from __future__ import annotations

__all__ = ["parameter_stability", "stability_label"]


def parameter_stability(trials, *, top_frac: float = 0.25) -> float:
    """Return a robustness score in [0, 1] for the optimum's neighbourhood.

    Parameters
    ----------
    trials:
        Iterable of objects with a ``.score`` attribute (e.g. ``OptimizeTrial``
        or ``OptimizeResult``), or plain floats/ints.
    top_frac:
        Fraction of trials (sorted by score, descending) to treat as the
        neighbourhood of the optimum.  Default: 0.25 (top quarter).

    Returns
    -------
    float
        1.0 = flat plateau (all top scores ≈ best), 0.0 = isolated spike.
    """
    # Accept both objects-with-.score and raw numbers.
    scores = []
    for t in trials:
        try:
            scores.append(float(t.score))
        except AttributeError:
            scores.append(float(t))

    n = len(scores)
    if n <= 1:
        return 1.0  # no evidence of spikiness with 0 or 1 data points

    scores.sort(reverse=True)
    best = scores[0]

    # Determine how many trials form the "top" neighbourhood.
    k = max(1, round(n * top_frac))
    top_scores = scores[:k]
    mean_top = sum(top_scores) / len(top_scores)

    # Normalise relative to the best score, handling sign.
    if best == 0.0:
        # Can't normalise; if all top scores are also 0 that's a plateau.
        return 1.0 if mean_top == 0.0 else 0.0

    if best > 0.0:
        ratio = mean_top / best
    else:
        # All scores are <= 0.  Negate so that "least negative" is "best".
        # A cluster near the best negative value → ratio near 1.
        neg_best = -best          # > 0
        neg_mean = -mean_top      # >= neg_best  (mean of top is the least negative)
        ratio = neg_best / neg_mean if neg_mean != 0.0 else 1.0

    # Clamp to [0, 1] for numerical safety.
    return float(min(max(ratio, 0.0), 1.0))


def stability_label(stability: float) -> str:
    """Classify a stability score into a plain-language label.

    >=0.8  → ``'plateau'``  (broad, robust optimum)
    >=0.5  → ``'ridge'``    (moderate sensitivity)
    < 0.5  → ``'spike'``    (fragile / overfit optimum)
    """
    if stability >= 0.8:
        return "plateau"
    if stability >= 0.5:
        return "ridge"
    return "spike"
