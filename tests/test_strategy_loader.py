"""Loading a user-authored strategy from a .py file."""

import pytest

from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.strategy_loader import load_strategy_from_file

_GOOD = """
from vike_trader_app.core.strategy import Strategy

class MyStrat(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
"""

_NONE = """
x = 42
"""


def test_loads_strategy_subclass(tmp_path):
    f = tmp_path / "mystrat.py"
    f.write_text(_GOOD)
    cls = load_strategy_from_file(str(f))
    assert issubclass(cls, Strategy)
    assert cls is not Strategy
    assert cls.__name__ == "MyStrat"


def test_loaded_strategy_is_instantiable(tmp_path):
    f = tmp_path / "mystrat.py"
    f.write_text(_GOOD)
    cls = load_strategy_from_file(str(f))
    inst = cls()
    assert isinstance(inst, Strategy)


def test_raises_when_no_strategy_found(tmp_path):
    f = tmp_path / "empty.py"
    f.write_text(_NONE)
    with pytest.raises(ValueError):
        load_strategy_from_file(str(f))
