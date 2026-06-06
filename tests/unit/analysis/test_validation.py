"""Cross-validation splitter tests (walk-forward, purged k-fold, combinatorial)."""

from itertools import combinations

import pytest

from vike_trader_app.analysis.validation import (
    combinatorial_purged_splits,
    purged_kfold_indices,
    walk_forward_splits,
)


def test_walk_forward_expanding_windows():
    splits = walk_forward_splits(10, 4)
    assert splits == [
        (0, 2, 2, 4),
        (0, 4, 4, 6),
        (0, 6, 6, 8),
        (0, 8, 8, 10),
    ]


def test_walk_forward_train_always_precedes_test():
    for tr_s, tr_e, te_s, te_e in walk_forward_splits(100, 5):
        assert tr_s == 0
        assert tr_e == te_s  # no gap, no overlap
        assert te_s < te_e


def test_walk_forward_anchored_is_default():
    assert walk_forward_splits(10, 4) == walk_forward_splits(10, 4, mode="anchored")


def test_walk_forward_rolling_fixed_width_windows():
    splits = walk_forward_splits(10, 4, mode="rolling")
    assert splits == [
        (0, 2, 2, 4),
        (2, 4, 4, 6),
        (4, 6, 6, 8),
        (6, 8, 8, 10),
    ]


def test_walk_forward_rejects_unknown_mode():
    with pytest.raises(ValueError):
        walk_forward_splits(10, 4, mode="sliding")


def test_walk_forward_window_invariants_both_modes():
    for n, k in [(10, 4), (100, 5), (37, 6), (60, 3), (24, 7)]:
        chunk = n // (k + 1)
        for mode in ("anchored", "rolling"):
            for tr_s, tr_e, te_s, te_e in walk_forward_splits(n, k, mode=mode):
                # ordering: 0 <= tr_s < tr_e == te_s < te_e <= n
                assert 0 <= tr_s < tr_e
                assert tr_e == te_s
                assert te_s < te_e <= n
                # train and test are disjoint, no look-ahead
                assert set(range(tr_s, tr_e)).isdisjoint(range(te_s, te_e))
                assert tr_e <= te_s  # all train indices < all test indices


def test_walk_forward_rolling_train_width_is_chunk():
    n, k = 100, 5
    chunk = n // (k + 1)
    splits = walk_forward_splits(n, k, mode="rolling")
    # non-first windows have a fixed-width train of exactly one chunk
    for tr_s, tr_e, _te_s, _te_e in splits[1:]:
        assert tr_e - tr_s == chunk


def test_walk_forward_anchored_train_width_grows():
    splits = walk_forward_splits(100, 5, mode="anchored")
    widths = [tr_e - tr_s for tr_s, tr_e, _, _ in splits]
    assert widths == sorted(widths)
    assert widths[0] < widths[-1]  # strictly expanding


def test_purged_kfold_partitions_test_folds():
    splits = purged_kfold_indices(10, 2, embargo=0)
    assert [te for _, te in splits] == [[0, 1, 2, 3, 4], [5, 6, 7, 8, 9]]


def test_purged_kfold_embargo_removes_window_after_test():
    splits = purged_kfold_indices(10, 2, embargo=1)
    train0, test0 = splits[0]
    assert test0 == [0, 1, 2, 3, 4]
    assert train0 == [6, 7, 8, 9]  # index 5 embargoed


def test_purged_kfold_train_and_test_disjoint():
    for train, test in purged_kfold_indices(20, 4, embargo=2):
        assert set(train).isdisjoint(test)


def test_combinatorial_split_count_is_n_choose_k():
    splits = combinatorial_purged_splits(12, n_groups=4, n_test_groups=2)
    assert len(splits) == len(list(combinations(range(4), 2)))  # == 6


def test_combinatorial_first_split_groups_are_test():
    splits = combinatorial_purged_splits(12, n_groups=4, n_test_groups=2, embargo=0)
    train, test = splits[0]
    assert test == [0, 1, 2, 3, 4, 5]  # groups {0,1} of size 3 each
    assert train == [6, 7, 8, 9, 10, 11]


def test_combinatorial_train_test_disjoint():
    for train, test in combinatorial_purged_splits(24, 6, 2, embargo=1):
        assert set(train).isdisjoint(test)
