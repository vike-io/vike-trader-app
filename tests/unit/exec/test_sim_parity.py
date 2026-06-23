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


# ---------------------------------------------------------------------------
# Intrabar SL+TP bracket cap
# ---------------------------------------------------------------------------

class _BracketStrategy(Strategy):
    """Buy on bar 0, arm a stop_sell at 95 + limit_sell at 115 on bar 1.

    Bar 2 straddles both triggers (low=90 <= 95 AND high=120 >= 115):
    engine caps the limit to ~0 (stop fills first), so only ONE closing fill
    happens and intrabar_both_hit == 1.
    """

    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            # Arm bracket — both will trigger on bar 2
            self.stop_sell(1.0, price=95.0)
            self.limit_sell(1.0, price=115.0)


def _bracket_bars():
    # bar 0: entry — fills at open=100 on bar 1
    # bar 1: stop+limit arms — next-open semantics: arms before strategy sees bar 2
    # bar 2: straddle bar — open=100 (between 95 and 115), high=120, low=90
    return [
        Bar(ts=0,        open=100, high=105, low=95,  close=102, volume=1.0),  # buy submitted
        Bar(ts=60_000,   open=100, high=105, low=95,  close=100, volume=1.0),  # entry fills; bracket armed
        Bar(ts=120_000,  open=100, high=120, low=90,  close=105, volume=1.0),  # straddles both stops
    ]


def test_intrabar_bracket_cap_parity():
    """Parity holds through the adversarial stop-first bracket-cap path.

    The stop at 95 fires first (adverse), consuming the entire size=1 position.
    The limit at 115 gets capped to 0 and is skipped. One closing Trade, one
    FillEvent — parity must hold AND intrabar_both_hit must be 1.
    """
    bus = EventBus()
    acc = Account()
    bus.subscribe(lambda ev: acc.apply_fill(ev) if isinstance(ev, FillEvent) else None)
    eng = BacktestEngine(_bracket_bars(), _BracketStrategy(), cash=10_000.0)
    SimulatedExecutionClient(eng, bus, venue="sim", symbol="X")
    res = eng.run()

    assert acc.trades == [t.pnl for t in res.trades]   # parity holds through bracket-cap
    assert res.intrabar_both_hit == 1                   # exactly the adversarial bar was exercised


# ---------------------------------------------------------------------------
# Liquidation force-close
# ---------------------------------------------------------------------------

class _LeveragedLong(Strategy):
    """Open a maximum leveraged long on bar 0; hold until liquidated."""

    def on_bar(self, bar):
        if self.index == 0:
            self.buy(100.0)   # leverage cap will trim to max; we need a large number


def _liquidation_bars():
    # bar 0: buy submitted, fills at open of bar 1 (=100)
    # bar 1: hold — equity fine, low=95 not enough to liquidate (95 > 94.73)
    # bar 2: liquidation bar — low=90 < 94.73 threshold triggers force-close
    return [
        Bar(ts=0,        open=100, high=105, low=98,  close=100, volume=1.0),  # buy submitted
        Bar(ts=60_000,   open=100, high=102, low=95,  close=100, volume=1.0),  # entry fills; safe
        Bar(ts=120_000,  open=100, high=102, low=90,  close=100, volume=1.0),  # liquidation fires
    ]


def test_liquidation_parity():
    """Parity holds through the _check_liquidation force-close path.

    Setup: leverage=10, maint_margin=0.05, cash=1000.
    Leverage cap: max_pos = 10 * 1000 / 100 = 100 shares.
    After buying 100 shares at 100: cash=-9000, notional=100*100=10000.
    Liq threshold: -9000 + 100*adverse <= 0.05 * 100 * adverse -> adverse <= 94.73.
    Bar 2 low=90 triggers force-close at adverse=90 -> one closing Trade, one FillEvent.
    """
    bus = EventBus()
    acc = Account()
    bus.subscribe(lambda ev: acc.apply_fill(ev) if isinstance(ev, FillEvent) else None)
    # leverage=10, maint_margin=0.05: cash=1000, leverage cap allows 100 shares at 100
    eng = BacktestEngine(
        _liquidation_bars(),
        _LeveragedLong(),
        cash=1_000.0,
        leverage=10.0,
        maint_margin=0.05,
    )
    SimulatedExecutionClient(eng, bus, venue="sim", symbol="X")
    res = eng.run()

    assert acc.trades == [t.pnl for t in res.trades]   # parity holds through liquidation fill
    assert len(res.trades) >= 1                         # at least the liquidation close trade
    assert eng.position.size == 0.0                     # position is flat after liquidation
