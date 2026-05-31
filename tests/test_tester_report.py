"""TesterReport.from_result computes the standardized metric set from an engine Result."""

from vike_trader_app.core.engine import Result
from vike_trader_app.core.model import Trade
from vike_trader_app.tester.report import TesterReport


def _t(pnl, fees=0.0):
    return Trade(entry_price=100.0, exit_price=100.0 + pnl, size=1.0, pnl=pnl, fees=fees)


def test_from_result_populates_metrics():
    eq = [10_000.0, 10_010.0, 10_006.0, 10_012.0]
    trades = [_t(10.0, 1.0), _t(-4.0, 1.0), _t(6.0, 1.0)]
    rep = TesterReport.from_result(Result(trades, eq, eq[-1]), periods_per_year=525_600)

    assert rep.n_trades == 3
    assert rep.net_profit == 12.0
    assert rep.gross_profit == 16.0
    assert rep.gross_loss == 4.0
    assert rep.profit_factor == 4.0
    assert rep.expected_payoff == 4.0
    assert rep.win_rate == 2 / 3
    assert rep.pct_profitable == 2 / 3
    assert rep.largest_win == 10.0
    assert rep.largest_loss == -4.0
    assert rep.consecutive_wins == 1
    assert rep.final_equity == 10_012.0
    assert rep.total_return == eq[-1] / eq[0] - 1.0
    d = rep.as_dict()
    assert d["net_profit"] == 12.0 and d["n_trades"] == 3


def test_empty_run_is_safe():
    rep = TesterReport.from_result(Result([], [10_000.0], 10_000.0))
    assert rep.n_trades == 0
    assert rep.profit_factor == 0.0
    assert rep.expected_payoff == 0.0
    assert rep.max_drawdown == 0.0
