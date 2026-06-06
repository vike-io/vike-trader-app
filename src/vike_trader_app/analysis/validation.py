"""Cross-validation splitters for time series (anti-overfitting).

- ``walk_forward_splits``: out-of-sample evaluation via anchored (expanding) or
  rolling (fixed-width sliding) train windows.
- ``purged_kfold_indices``: k-fold with an embargo after each test fold
  (López de Prado, *Advances in Financial Machine Learning*, ch. 7).
- ``combinatorial_purged_splits``: all C(groups, test_groups) train/test paths,
  the basis for CSCV / PBO.

Embargo here removes a window *after* each test block from training (point-in-time
observations, so there is no label-overlap purge to apply before the block).
"""

from itertools import combinations


def walk_forward_splits(n: int, n_splits: int, *, mode: str = "anchored"):
    """Walk-forward splits as ``(train_start, train_end, test_start, test_end)``.

    ``mode='anchored'`` (default): expanding window, ``train_start`` always 0.
    ``mode='rolling'``: fixed-width train of one chunk immediately preceding the
    test window (``train_start = max(0, test_start - chunk)``).
    Any other ``mode`` raises ``ValueError``.
    """
    if mode not in ("anchored", "rolling"):
        raise ValueError(f"unknown mode: {mode!r} (expected 'anchored' or 'rolling')")
    chunk = n // (n_splits + 1)
    splits = []
    for s in range(1, n_splits + 1):
        test_start = s * chunk
        test_end = n if s == n_splits else (s + 1) * chunk
        train_start = 0 if mode == "anchored" else max(0, test_start - chunk)
        splits.append((train_start, test_start, test_start, test_end))
    return splits


def _group_bounds(n: int, n_groups: int):
    return [(g * n // n_groups, (g + 1) * n // n_groups) for g in range(n_groups)]


def purged_kfold_indices(n: int, k: int, embargo: int = 0):
    """k contiguous test folds; train excludes the test fold + an embargo window after it."""
    splits = []
    for t0, t1 in _group_bounds(n, k):
        test = list(range(t0, t1))
        embargo_end = min(t1 + embargo, n)
        train = [i for i in range(n) if i < t0 or i >= embargo_end]
        splits.append((train, test))
    return splits


def combinatorial_purged_splits(n: int, n_groups: int, n_test_groups: int, embargo: int = 0):
    """All ``C(n_groups, n_test_groups)`` train/test paths with per-test-group embargo."""
    bounds = _group_bounds(n, n_groups)
    splits = []
    for combo in combinations(range(n_groups), n_test_groups):
        test = sorted(i for g in combo for i in range(*bounds[g]))
        test_set = set(test)
        purged = set()
        for g in combo:
            _, end = bounds[g]
            purged.update(range(end, min(end + embargo, n)))
        train = [i for i in range(n) if i not in test_set and i not in purged]
        splits.append((train, test))
    return splits
