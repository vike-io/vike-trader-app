"""Unit tests for the report-extras analytics (returns, MFE/MAE, histogram, CSV)."""

from vike_trader_app.analysis import report_extras as R
from vike_trader_app.core.model import Bar, Trade


def test_trade_returns():
    trades = [Trade(entry_price=100.0, exit_price=110.0, size=1.0, pnl=10.0),
              Trade(entry_price=100.0, exit_price=95.0, size=2.0, pnl=-10.0)]
    assert R.trade_returns(trades) == [0.1, -0.05]


def test_mfe_mae_long():
    # one long trade entered at bar 0 (price 100), exited at bar 2; high 120, low 90 in between
    bars = [Bar(ts=0, open=100, high=105, low=100, close=100),
            Bar(ts=60_000, open=100, high=120, low=90, close=110),
            Bar(ts=120_000, open=110, high=112, low=108, close=110)]
    t = Trade(entry_price=100.0, exit_price=110.0, size=1.0, pnl=10.0,
              entry_ts=0, exit_ts=120_000)
    (mfe, mae), = R.mfe_mae([t], bars)
    assert abs(mfe - 0.20) < 1e-9   # high 120 -> +20%
    assert abs(mae + 0.10) < 1e-9   # low 90  -> -10%


def test_returns_histogram_counts_all():
    edges, counts = R.returns_histogram([0.0, 0.1, 0.1, 0.2], bins=4)
    assert len(edges) == 5
    assert sum(counts) == 4


def test_returns_histogram_empty():
    assert R.returns_histogram([]) == ([], [])


def test_report_to_csv_has_metrics_and_trades():
    import math

    from vike_trader_app.core.strategy_loader import load_strategy_from_string
    from vike_trader_app.tester import StrategyTester, TesterConfig

    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + (i % 7))
            for i in range(40)]
    code = ("from vike_trader_app.core.strategy import Strategy\n\n"
            "class S(Strategy):\n"
            "    def on_bar(self, bar):\n"
            "        if self.index == 1:\n"
            "            self.buy(1.0)\n"
            "        elif self.index == 5:\n"
            "            self.close()\n")
    rep = StrategyTester(load_strategy_from_string(code)(), bars, TesterConfig(taker_fee=0.0)).run()
    csv = R.report_to_csv(rep)
    assert csv.startswith("metric,value")
    assert "n_trades," in csv
    assert "trade,side,entry_ts,exit_ts,entry,exit,size,pnl,fees" in csv
    assert not math.isnan(rep.total_return)
