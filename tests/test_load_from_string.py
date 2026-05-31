"""load_strategy_from_string: pre-flight gate + temp-file load."""

import pytest

from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.strategy_loader import load_strategy_from_string

_GOOD = """
from vike_trader_app.core.strategy import Strategy

class MyStrat(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
"""


def test_loads_valid_strategy_from_string():
    cls = load_strategy_from_string(_GOOD)
    assert issubclass(cls, Strategy) and cls is not Strategy
    assert hasattr(cls(), "on_bar")


def test_rejects_malicious_source():
    bad = ("import os\nfrom vike_trader_app.core.strategy import Strategy\n"
           "class S(Strategy):\n    def on_bar(self, bar): pass\n")
    with pytest.raises(ValueError):
        load_strategy_from_string(bad)


def test_validate_false_bypasses_gate():
    # a dunder access trips the gate but is harmless to exec; validate=False must still load.
    code = ("from vike_trader_app.core.strategy import Strategy\n_x = (1).__class__\n"
            "class S(Strategy):\n    def on_bar(self, bar): pass\n")
    cls = load_strategy_from_string(code, validate=False)
    assert issubclass(cls, Strategy)
