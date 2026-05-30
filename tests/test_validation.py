"""Cross-validation splitter tests (walk-forward, purged k-fold, combinatorial)."""

from itertools import combinations

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
