"""Unit tests for the Qt-free command-palette fuzzy matcher (Phase 5)."""

from vike_trader_app.ui.fuzzy import filter_items, fuzzy_score


def test_empty_query_matches_all_with_zero():
    assert fuzzy_score("", "anything") == 0
    items = [("Chart", 1), ("Studio", 2)]
    assert filter_items("", items) == items


def test_non_subsequence_is_no_match():
    assert fuzzy_score("zx", "Chart") is None


def test_subsequence_matches():
    assert fuzzy_score("crt", "Chart") is not None       # c-h-a-r-t contains c,r,t in order


def test_word_boundary_outranks_buried_match():
    items = [("Open Screener", "a"), ("New chart", "b")]
    # "nc" = New chart (two word-starts) should beat the buried n/c in "Open Screener"
    ranked = filter_items("nc", items)
    assert ranked[0][1] == "b"


def test_contiguous_outranks_scattered():
    assert fuzzy_score("cha", "Chart") > fuzzy_score("cha", "Custom heat after")


def test_filter_drops_non_matches_and_sorts():
    items = [("Save workspace", 1), ("New chart", 2), ("Open Studio", 3)]
    ranked = filter_items("save", items)
    assert [p for _l, p in ranked] == [1]


def test_filter_preserves_order_for_equal_scores():
    items = [("Alpha", 1), ("Alps", 2)]      # both match "alp" identically at the start
    ranked = filter_items("alp", items)
    assert [p for _l, p in ranked] == [1, 2]
