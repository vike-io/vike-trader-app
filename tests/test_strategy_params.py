"""Strategies can declare a PARAM_GRID and be built with overridden params."""

from vike_trader_app.core.strategy import Strategy


def test_param_grid_defaults_empty():
    assert Strategy.PARAM_GRID == {}


def test_make_sets_params_as_attributes():
    class S(Strategy):
        x = 1
        y = 2

    inst = S.make(x=5, y=9)
    assert inst.x == 5
    assert inst.y == 9


def test_make_without_params_returns_instance():
    inst = Strategy.make()
    assert isinstance(inst, Strategy)
