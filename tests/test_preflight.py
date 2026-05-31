"""AST pre-flight gate."""

from vike_trader_app.core.sandbox.preflight import check_strategy_source

_GOOD = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index % 2 == 0:
            self.buy(1.0)
"""


def test_clean_strategy_passes():
    assert check_strategy_source(_GOOD) == []


def test_forbidden_import_flagged():
    assert any("import not allowed" in p for p in check_strategy_source("import os\n"))


def test_data_layer_import_flagged():
    assert any("import not allowed" in p for p in
               check_strategy_source("from vike_trader_app.data.store import Store\n"))


def test_dunder_escape_flagged():
    assert any("dunder" in p for p in check_strategy_source("x = ().__class__.__subclasses__()\n"))


def test_forbidden_name_flagged():
    assert any("not allowed" in p for p in check_strategy_source("y = eval('1+1')\n"))


def test_allowed_imports_ok():
    assert check_strategy_source("import math\nimport numpy as np\nfrom datetime import datetime\n") == []


def test_normal_loops_allowed():
    assert check_strategy_source("def f():\n    while True:\n        pass\n") == []


def test_syntax_error_reported():
    assert any("syntax error" in p for p in check_strategy_source("def f(:\n"))
