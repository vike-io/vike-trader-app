"""Parity gate: PnL derived from the SimulatedExecutionClient's FillEvent stream == engine.trades."""

import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent
from vike_trader_app.exec.sim_client import SimulatedExecutionClient


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 5, low=min(o, c) - 5, close=c, volume=1.0)


def _ramp():
    # ascending then descending so longs, shorts, adds and flips all realize non-trivial PnL
    closes = [100, 110, 120, 130, 120, 110, 100, 110]
    return [_bar(i * 60_000, c, c) for i, c in enumerate(closes)]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()


class _ScaleInThenOut(Strategy):
    def on_bar(self, bar):
        if self.index in (0, 1):
            self.buy(1.0)            # two adds -> averaged cost
        elif self.index == 4:
            self.close()


class _LongThenFlipShort(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.order_target_shares(-1.0)   # close 1 and open -1 in one order (flip)
        elif self.index == 6:
            self.close()


class _ShortThenCover(Strategy):
    def on_bar(self, bar):
        if self.index == 1:
            self.sell(2.0)
        elif self.index == 5:
            self.close()


@pytest.mark.parametrize("strat_cls", [_BuyThenClose, _ScaleInThenOut, _LongThenFlipShort, _ShortThenCover])
def test_fillevent_derived_pnl_matches_engine_trades(strat_cls):
    bus = EventBus()
    acc = Account()
    bus.subscribe(lambda ev: acc.apply_fill(ev) if isinstance(ev, FillEvent) else None)
    eng = BacktestEngine(_ramp(), strat_cls(), cash=10_000.0, taker_fee=0.001)
    SimulatedExecutionClient(eng, bus, venue="sim", symbol="X")
    res = eng.run()

    assert acc.trades == [t.pnl for t in res.trades]                 # bit-for-bit per trade
    assert acc.realized_pnl == pytest.approx(sum(t.pnl for t in res.trades), abs=0.0)
    assert len(res.trades) >= 1                                      # the scenario actually traded


def test_parity_holds_with_slippage_and_multiplier():
    bus = EventBus()
    acc = Account(multiplier=5.0)
    bus.subscribe(lambda ev: acc.apply_fill(ev) if isinstance(ev, FillEvent) else None)
    eng = BacktestEngine(_ramp(), _BuyThenClose(), cash=100_000.0, taker_fee=0.001,
                         slippage=0.0005, multiplier=5.0)
    SimulatedExecutionClient(eng, bus, symbol="X")
    res = eng.run()
    assert acc.trades == [t.pnl for t in res.trades]                 # adverse fill price flows through the FillEvent
