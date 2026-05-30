"""Phase 2 engine-side correctness: target sizing, leverage cap, liquidation, cashflows."""

import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


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
