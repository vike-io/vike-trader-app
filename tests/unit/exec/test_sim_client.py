"""SimulatedExecutionClient publishes a FillEvent per engine fill, synchronously, no behavior change."""

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent
from vike_trader_app.exec.sim_client import SimulatedExecutionClient


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


def _fills(bus):
    seen = []
    bus.subscribe(lambda ev: seen.append(ev) if isinstance(ev, FillEvent) else None)
    return seen


def test_publishes_one_fillevent_per_fill():
    bus = EventBus()
    seen = _fills(bus)
    eng = BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001)
    SimulatedExecutionClient(eng, bus, venue="sim", symbol="BTCUSDT")
    eng.run()
    assert len(seen) == 2
    assert (seen[0].side, seen[0].last_qty, seen[0].last_px, seen[0].liquidity_side) == (+1, 1.0, 110.0, "taker")
    assert (seen[1].side, seen[1].last_qty, seen[1].last_px) == (-1, 1.0, 130.0)
    assert seen[0].symbol == "BTCUSDT" and seen[0].venue == "sim"
    assert seen[0].commission > 0
    assert seen[0].trade_id != seen[1].trade_id  # unique dedup keys


def test_client_does_not_change_engine_results():
    base = BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001).run()
    eng = BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001)
    SimulatedExecutionClient(eng, EventBus(), symbol="X")
    hooked = eng.run()
    assert hooked.equity_curve == base.equity_curve
    assert [t.pnl for t in hooked.trades] == [t.pnl for t in base.trades]
