from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


class _BuyHoldClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


def _bars():
    return [Bar(ts=t, open=10 + t, high=12 + t, low=9 + t, close=11 + t) for t in (0, 60, 120, 180)]


def test_step_behavior_pinned_with_cashflows_and_fees():
    eng = SingleSymbolEngine(_bars(), _BuyHoldClose(), taker_fee=0.001, cash=1000.0,
                         cashflows=[5.0, 0.0, 0.0, 0.0])
    result = eng.run()
    # Pins the exact equity curve + the one round-trip trade (entry next-open after bar0, exit after bar2).
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == 70.0   # bar1 open (10+60)
    assert result.trades[0].exit_price == 190.0   # bar3 open (10+180)
    assert round(result.final_equity, 6) == round(eng.equity_now(), 6)
    assert result.equity_curve[0] == 1005.0       # cash 1000 + cashflow 5 (flat at bar0)
