"""TesterConfig + StrategyTester facade."""

from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.tester import Backtester, StrategyTester, TesterConfig, TesterReport


def test_config_defaults_and_engine_kwargs():
    c = TesterConfig()
    assert c.cash == 10_000.0 and c.taker_fee is None and c.slippage == 0.0
    assert c.multiplier == 1.0 and c.leverage is None
    kw = TesterConfig(taker_fee=0.001, maker_fee=0.0005, slippage=0.0002,
                      cash=5_000.0, multiplier=2.0).engine_kwargs()
    assert kw == {
        "fee_rate": 0.0, "cash": 5_000.0, "timeframes": None, "slippage": 0.0002,
        "maker_fee": 0.0005, "taker_fee": 0.001, "multiplier": 2.0,
        "leverage": None, "maint_margin": 0.0, "cashflows": None,
    }


class _BuyHold(Strategy):
    def on_bar(self, bar):  # noqa: ARG002
        if self.index == 0:
            self.buy(1.0)


def _bars():
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    return [Bar(ts=i * 60_000, open=closes[i], high=closes[i] + 1, low=closes[i] - 1,
                close=closes[i]) for i in range(len(closes))]


def test_backtester_run_matches_engine_and_returns_report():
    cfg = TesterConfig(taker_fee=0.001, cash=10_000.0)
    rep = Backtester(_BuyHold(), _bars(), cfg).run()
    expected = SingleSymbolEngine(_bars(), _BuyHold(), **cfg.engine_kwargs()).run()
    assert isinstance(rep, TesterReport)
    assert rep.equity_curve == expected.equity_curve
    assert rep.final_equity == expected.final_equity
    assert rep.n_trades == len(expected.trades)


def test_strategy_tester_facade_run_delegates():
    cfg = TesterConfig(taker_fee=0.001)
    rep = StrategyTester(_BuyHold(), _bars(), cfg).run()
    assert isinstance(rep, TesterReport)
    assert rep.total_return == rep.equity_curve[-1] / rep.equity_curve[0] - 1.0
