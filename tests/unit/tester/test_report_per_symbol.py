"""TesterReport carries per_symbol_pnl through from_result (for the By-Symbol view)."""

from vike_trader_app.tester.report import TesterReport


class _Result:
    def __init__(self, per_symbol=None):
        self.trades = []
        self.equity_curve = [1000.0, 1000.0]
        self.final_equity = 1000.0
        if per_symbol is not None:
            self.per_symbol_pnl = per_symbol


def test_from_result_copies_per_symbol_pnl_when_present():
    rep = TesterReport.from_result(_Result({"A": 5.0, "B": -2.0}))
    assert rep.per_symbol_pnl == {"A": 5.0, "B": -2.0}


def test_from_result_per_symbol_pnl_none_when_absent():
    rep = TesterReport.from_result(_Result(None))
    assert rep.per_symbol_pnl is None
