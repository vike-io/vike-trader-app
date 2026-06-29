"""Phase 2 engine-side correctness: target sizing, leverage cap, liquidation, cashflows."""

import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def _flat_bars(prices, ts_step=60_000):
    return [Bar(ts=i * ts_step, open=p, high=p + 1, low=p - 1, close=p)
            for i, p in enumerate(prices)]


def test_order_target_percent_reaches_notional():
    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.order_target_percent(0.5)  # 50% of equity into the position

    prices = [100.0, 100.0, 100.0]   # open==close==100 throughout
    eng = BacktestEngine(_flat_bars(prices), S(), cash=10_000.0)
    eng.run()
    # decision at bar 0 (equity 10_000, price 100) -> target 50 shares; fills at bar 1 open=100
    assert eng.position.size == pytest.approx(50.0)


def test_order_target_value_reaches_shares():
    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.order_target_value(2500.0)

    prices = [100.0, 100.0, 100.0]
    eng = BacktestEngine(_flat_bars(prices), S(), cash=10_000.0)
    eng.run()
    # 2500 / (100 * 1.0) = 25 shares; fills at bar 1 open=100
    assert eng.position.size == pytest.approx(25.0)


def test_leverage_cap_limits_position():
    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1000.0)   # way over the cap

    prices = [100.0, 100.0, 100.0]
    eng = BacktestEngine(_flat_bars(prices), S(), cash=10_000.0, leverage=3.0)
    eng.run()
    # max notional = 3 * 10_000 = 30_000 at price 100 -> 300 shares cap
    assert eng.position.size == pytest.approx(300.0)


def test_liquidation_force_closes_on_crash():
    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(50.0)   # 50 @100 = 5000 notional on 1000 cash

    bars = [
        Bar(ts=0, open=100.0, high=101.0, low=100.0, close=100.0),
        Bar(ts=60_000, open=100.0, high=101.0, low=100.0, close=100.0),  # entry fills here
        Bar(ts=120_000, open=100.0, high=101.0, low=50.0, close=55.0),   # crash low -> liquidation
        Bar(ts=180_000, open=55.0, high=56.0, low=54.0, close=55.0),
    ]
    eng = BacktestEngine(bars, S(), cash=1_000.0, leverage=10.0, maint_margin=0.05)
    eng.run()
    assert eng.position.size == 0.0        # liquidated
    assert len(eng.trades) == 1
    assert eng.trades[0].exit_price < 100.0  # closed at the crash extreme (with slippage)
