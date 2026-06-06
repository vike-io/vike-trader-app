"""Optimization-surface data-prep tests.

Pivots optimization trials over two chosen parameter axes into a row-major
Z-grid (z[y_index][x_index], mirroring heatmap's scores[y][x]), fixing the
other params.
"""

from dataclasses import dataclass

import pytest

from vike_trader_app.analysis.surface import Surface, best_axes, surface_from_trials


@dataclass
class _T:
    """Stand-in for tester OptimizeTrial / analysis OptimizeResult."""

    params: dict
    score: float


def test_surface_shape_and_values():
    trials = [
        _T({"fast": 5, "slow": 20}, 1.0),
        _T({"fast": 5, "slow": 30}, 2.0),
        _T({"fast": 10, "slow": 20}, 3.0),
        _T({"fast": 10, "slow": 30}, 4.0),
    ]
    surf = surface_from_trials(trials, "fast", "slow")
    assert isinstance(surf, Surface)
    assert surf.param_x == "fast" and surf.param_y == "slow"
    assert surf.values_x == [5, 10]          # sorted distinct x
    assert surf.values_y == [20, 30]         # sorted distinct y
    assert len(surf.z) == len(surf.values_y)  # rows = y values
    assert all(len(row) == len(surf.values_x) for row in surf.z)  # cols = x values
    # z[y_index][x_index]
    assert surf.z[0][0] == 1.0  # (fast=5,  slow=20)
    assert surf.z[1][0] == 2.0  # (fast=5,  slow=30)
    assert surf.z[0][1] == 3.0  # (fast=10, slow=20)
    assert surf.z[1][1] == 4.0  # (fast=10, slow=30)
    assert surf.fixed == {}


def test_surface_accepts_plain_tuples():
    trials = [
        ({"fast": 5, "slow": 20}, 1.5),
        ({"fast": 5, "slow": 30}, 2.5),
        ({"fast": 10, "slow": 20}, 3.5),
        ({"fast": 10, "slow": 30}, 4.5),
    ]
    surf = surface_from_trials(trials, "fast", "slow")
    assert surf.values_x == [5, 10]
    assert surf.values_y == [20, 30]
    assert surf.z[1][1] == 4.5


def test_surface_takes_max_on_collision():
    # two trials land on the same (x, y) cell -> MAX wins
    trials = [
        _T({"fast": 5, "slow": 20}, 1.0),
        _T({"fast": 5, "slow": 20}, 7.0),
        _T({"fast": 5, "slow": 30}, 2.0),
        _T({"fast": 10, "slow": 20}, 3.0),
        _T({"fast": 10, "slow": 30}, 4.0),
    ]
    surf = surface_from_trials(trials, "fast", "slow")
    assert surf.z[0][0] == 7.0


def test_surface_none_for_missing_combo():
    # (fast=10, slow=20) is absent -> that cell is None
    trials = [
        _T({"fast": 5, "slow": 20}, 1.0),
        _T({"fast": 5, "slow": 30}, 2.0),
        _T({"fast": 10, "slow": 30}, 4.0),
    ]
    surf = surface_from_trials(trials, "fast", "slow")
    assert surf.values_x == [5, 10]
    assert surf.values_y == [20, 30]
    # the missing pair
    xi = surf.values_x.index(10)
    yi = surf.values_y.index(20)
    assert surf.z[yi][xi] is None
    # present pairs are filled
    assert surf.z[surf.values_y.index(20)][surf.values_x.index(5)] == 1.0
    assert surf.z[surf.values_y.index(30)][surf.values_x.index(10)] == 4.0


def test_surface_fixed_filters_third_param():
    # 3-param grid; fix the third param ('atr') and surface over fast x slow.
    trials = [
        _T({"fast": 5, "slow": 20, "atr": 1}, 1.0),
        _T({"fast": 5, "slow": 20, "atr": 2}, 99.0),  # excluded by fixed
        _T({"fast": 5, "slow": 30, "atr": 1}, 2.0),
        _T({"fast": 10, "slow": 20, "atr": 1}, 3.0),
        _T({"fast": 10, "slow": 30, "atr": 1}, 4.0),
        _T({"fast": 99, "slow": 99, "atr": 2}, 50.0),  # excluded; must not widen axes
    ]
    surf = surface_from_trials(trials, "fast", "slow", fixed={"atr": 1})
    assert surf.fixed == {"atr": 1}
    # axes only span trials matching fixed
    assert surf.values_x == [5, 10]
    assert surf.values_y == [20, 30]
    assert len(surf.z) == 2 and all(len(row) == 2 for row in surf.z)
    # atr=2 trial on (5,20) must be ignored -> 1.0, not 99.0
    assert surf.z[0][0] == 1.0
    assert surf.z[1][1] == 4.0


def test_best_axes_picks_first_two_multivalued_keys():
    grid = {
        "fast": [5, 10],
        "single": [42],          # only one value -> skipped
        "slow": [20, 30, 40],
        "atr": [1, 2],
    }
    assert best_axes(grid) == ("fast", "slow")


def test_best_axes_fallback_to_first_two_keys():
    # no key has >= 2 values -> fall back to first two keys (insertion order)
    grid = {"a": [1], "b": [2], "c": [3]}
    assert best_axes(grid) == ("a", "b")


def test_best_axes_raises_when_fewer_than_two_params():
    with pytest.raises(ValueError):
        best_axes({"only": [1, 2, 3]})
