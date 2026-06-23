"""OrderRouter: the Strategy->engine seam. gate=None is a transparent pass-through; a gate gates opens."""

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.order_router import OrderRouter
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.strategy_engine import StrategyEngine
from vike_trader_app.exec.risk import RiskGate, RiskLimits


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars():
    return [_bar(0, 100, 100), _bar(60_000, 110, 110), _bar(120_000, 120, 120), _bar(180_000, 130, 130)]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


def test_router_satisfies_strategy_engine_protocol():
    eng = BacktestEngine(_bars(), Strategy())
    assert isinstance(OrderRouter(eng), StrategyEngine)


def test_passthrough_router_is_identical_to_direct_engine():
    # baseline: strategy bound directly to its engine
    base = BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001).run()
    # routed: same engine, but the strategy submits through OrderRouter(engine, gate=None)
    strat = _BuyThenClose()
    eng = BacktestEngine(_bars(), strat, cash=10_000.0, taker_fee=0.001)
    strat._engine = OrderRouter(eng, None)        # transparent pass-through
    routed = eng.run()
    assert routed.equity_curve == base.equity_curve
    assert [t.pnl for t in routed.trades] == [t.pnl for t in base.trades]
    assert routed.final_equity == base.final_equity


def test_gate_denies_an_over_notional_open():
    # a RiskGate with a tiny per-order notional cap blocks the buy -> no position is opened
    strat = _BuyThenClose()
    eng = BacktestEngine(_bars(), strat, cash=10_000.0)
    strat._engine = OrderRouter(eng, RiskGate(RiskLimits(max_notional_per_order=10.0)), symbol="X")
    res = eng.run()
    # buy 1 @ ~110 = 110 notional > 10 -> denied; no trades, flat
    assert res.trades == []
    assert eng.position.size == 0.0


def test_gate_rounds_size_to_lot_on_an_open():
    strat = _BuyThenClose()
    eng = BacktestEngine(_bars(), strat, cash=10_000.0)
    # lot_size 0.5 floors-or-rounds the buy(1.0) to a valid lot; 1.0 is already a multiple -> unchanged here
    strat._engine = OrderRouter(eng, RiskGate(RiskLimits(lot_size=0.5)), symbol="X")
    res = eng.run()
    assert len(res.trades) == 1          # the round-trip still happens
    assert res.trades[0].size == 1.0


def test_closes_and_targets_pass_through_even_with_a_gate():
    # a HALTED-style deny must not prevent closing; closes bypass the gate
    class _OpenThenClose(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0)
            elif self.index == 2:
                self.close()
    strat = _OpenThenClose()
    eng = BacktestEngine(_bars(), strat, cash=10_000.0)
    # gate allows the open (no caps); the point is close() routes straight through
    strat._engine = OrderRouter(eng, RiskGate(RiskLimits()), symbol="X")
    res = eng.run()
    assert len(res.trades) == 1 and eng.position.size == 0.0
