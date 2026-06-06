"""Tests for parameter_stability and stability_label (analysis/stability.py)."""

import pytest

from vike_trader_app.analysis.stability import parameter_stability, stability_label


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _T:
    """Minimal trial-like object with a .score attribute."""
    def __init__(self, score):
        self.score = score


def _trials(*scores):
    return [_T(s) for s in scores]


# ---------------------------------------------------------------------------
# parameter_stability — edge / guard cases
# ---------------------------------------------------------------------------

def test_stability_empty_returns_one():
    assert parameter_stability([]) == 1.0


def test_stability_single_trial_returns_one():
    assert parameter_stability(_trials(5.0)) == 1.0


def test_stability_two_equal_scores_returns_one():
    result = parameter_stability(_trials(3.0, 3.0))
    assert result == pytest.approx(1.0)


def test_stability_accepts_raw_numbers():
    """plain floats are accepted as well as .score objects."""
    assert parameter_stability([1.0, 1.0, 1.0]) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# parameter_stability — plateau vs spike ordering
# ---------------------------------------------------------------------------

def test_plateau_high_stability():
    """All top scores equal the best → ratio = 1.0 (flat plateau)."""
    trials = _trials(10.0, 10.0, 9.9, 9.8, 9.7, 1.0)
    s = parameter_stability(trials, top_frac=0.25)
    assert s >= 0.9, f"expected plateau, got {s:.3f}"


def test_spike_low_stability():
    """Best = 10, all neighbours ≈ 1 → very low stability.

    With top_frac=0.5, the top half of 8 trials is 4: [10, 1, 1, 1].
    mean(top) = 3.25, ratio = 3.25/10 = 0.325 — clearly < 0.5.
    """
    trials = _trials(10.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    s = parameter_stability(trials, top_frac=0.5)
    assert s < 0.5, f"expected spike, got {s:.3f}"


def test_spike_lower_than_plateau():
    """Spike scenario must score strictly lower than plateau scenario."""
    plateau = parameter_stability(_trials(10.0, 9.9, 9.8, 9.7, 9.6, 9.5), top_frac=0.25)
    spike   = parameter_stability(_trials(10.0, 1.0, 1.0, 1.0, 1.0, 1.0), top_frac=0.25)
    assert spike < plateau


def test_stability_in_unit_interval():
    """Result must always be within [0, 1]."""
    for trials in [
        _trials(10.0, 1.0, 0.5),
        _trials(-1.0, -2.0, -3.0),
        _trials(0.0, 0.0, 0.0),
        _trials(5.0, 5.0, 5.0, 5.0),
    ]:
        s = parameter_stability(trials)
        assert 0.0 <= s <= 1.0, f"out of range: {s}"


# ---------------------------------------------------------------------------
# parameter_stability — negative scores
# ---------------------------------------------------------------------------

def test_negative_plateau():
    """All top scores tightly clustered near the best (least-negative) → high stability."""
    trials = _trials(-1.0, -1.1, -1.2, -1.3, -10.0, -20.0)
    s = parameter_stability(trials, top_frac=0.25)
    assert s >= 0.8, f"expected negative plateau to be stable, got {s:.3f}"


def test_negative_spike():
    """Best is least-negative but neighbors are very negative → low stability."""
    trials = _trials(-1.0, -9.0, -9.0, -9.0, -9.0, -9.0, -9.0, -9.0)
    s = parameter_stability(trials, top_frac=0.25)
    assert s < 0.5, f"expected negative spike to be unstable, got {s:.3f}"


# ---------------------------------------------------------------------------
# parameter_stability — zero-best guard
# ---------------------------------------------------------------------------

def test_zero_best_no_exception():
    """best == 0 must not raise; returns 1.0 when all top zeros."""
    assert parameter_stability(_trials(0.0, 0.0, 0.0)) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# stability_label
# ---------------------------------------------------------------------------

def test_label_plateau():
    assert stability_label(1.0) == "plateau"
    assert stability_label(0.8) == "plateau"


def test_label_ridge():
    assert stability_label(0.79) == "ridge"
    assert stability_label(0.5) == "ridge"


def test_label_spike():
    assert stability_label(0.49) == "spike"
    assert stability_label(0.0) == "spike"


def test_label_matches_stability_for_plateau_scenario():
    trials = _trials(10.0, 9.9, 9.8, 9.7, 9.6, 9.5)
    s = parameter_stability(trials, top_frac=0.25)
    assert stability_label(s) == "plateau"


def test_label_matches_stability_for_spike_scenario():
    """top_frac=0.5 drags the top-half mean down far enough to score as spike."""
    trials = _trials(10.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0)
    s = parameter_stability(trials, top_frac=0.5)
    assert stability_label(s) == "spike"
