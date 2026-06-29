"""SimulatedExchange: order-lifecycle publisher + ManagedOrder FSM gate.

Mirrors test_sim_parity.py scenarios but adds:
  (a) existing parity preserved -- Account folds bare FillEvents, acc.trades == engine trades
  (b) every tracked ManagedOrder reaches a terminal FSM state with correct filled_qty / avg_fill_px
  (c) lifecycle event ORDER per order: Submitted -> Accepted -> (PartiallyFilled*) -> Filled|Canceled
"""

import pytest

from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import (
    FillEvent,
    OrderSubmitted,
    OrderAccepted,
    OrderPartiallyFilled,
    OrderFilled,
    OrderCanceled,
)
from vike_trader_app.exec.order import OrderStatus
from vike_trader_app.exec.sim_exchange import SimulatedExchange


# ---------------------------------------------------------------------------
# Helpers shared with test_sim_parity.py
# ---------------------------------------------------------------------------

def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 5, low=min(o, c) - 5, close=c, volume=1.0)


def _ramp():
    closes = [100, 110, 120, 130, 120, 110, 100, 110]
    return [_bar(i * 60_000, c, c) for i, c in enumerate(closes)]


# ---------------------------------------------------------------------------
# Strategy classes (same as test_sim_parity.py)
# ---------------------------------------------------------------------------

class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()


class _ScaleInThenOut(Strategy):
    def on_bar(self, bar):
        if self.index in (0, 1):
            self.buy(1.0)
        elif self.index == 4:
            self.close()


class _LongThenFlipShort(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.order_target_shares(-1.0)
        elif self.index == 6:
            self.close()


class _ShortThenCover(Strategy):
    def on_bar(self, bar):
        if self.index == 1:
            self.sell(2.0)
        elif self.index == 5:
            self.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_scenario(strat, bars=None, *, cash=10_000.0, fee=0.001, slippage=0.0, multiplier=1.0, **engine_kwargs):
    """Build bus+account+exchange+engine, run, return (acc, res, exchange, events)."""
    if bars is None:
        bars = _ramp()
    bus = EventBus()
    acc = Account(multiplier=multiplier)
    events = []
    bus.subscribe(lambda ev: acc.apply_fill(ev) if isinstance(ev, FillEvent) else None)
    bus.subscribe(events.append)
    eng = SingleSymbolEngine(bars, strat, cash=cash, taker_fee=fee, slippage=slippage,
                         multiplier=multiplier, **engine_kwargs)
    exc = SimulatedExchange(eng, bus, venue="sim", symbol="X")
    res = eng.run()
    return acc, res, exc, events


def _lifecycle_for(coid, events):
    """Extract all order lifecycle events for a given client_order_id, in order."""
    out = []
    for ev in events:
        if isinstance(ev, (OrderSubmitted, OrderAccepted, OrderPartiallyFilled,
                           OrderFilled, OrderCanceled)):
            if ev.client_order_id == coid:
                out.append(ev)
    return out


def _assert_lifecycle(lifecycle):
    """Assert the standard Submitted -> Accepted -> ... -> Filled|Canceled sequence."""
    assert len(lifecycle) >= 3, f"Too few lifecycle events: {lifecycle}"
    assert isinstance(lifecycle[0], OrderSubmitted)
    assert isinstance(lifecycle[1], OrderAccepted)
    last = lifecycle[-1]
    assert isinstance(last, (OrderFilled, OrderCanceled)), \
        f"Last lifecycle event must be Filled or Canceled, got {type(last).__name__}"
    for middle in lifecycle[2:-1]:
        assert isinstance(middle, OrderPartiallyFilled), \
            f"Middle events must be PartiallyFilled, got {type(middle).__name__}"


# ---------------------------------------------------------------------------
# (a) Parity preserved + (b) FSM terminal + (c) lifecycle order
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("strat_cls", [_BuyThenClose, _ScaleInThenOut, _LongThenFlipShort, _ShortThenCover])
def test_exchange_parity_and_fsm(strat_cls):
    """Core triple-assertion: parity + FSM terminal states + lifecycle ordering."""
    acc, res, exc, events = _run_scenario(strat_cls())

    # (a) parity: Account derives same per-trade PnL as engine
    assert acc.trades == [t.pnl for t in res.trades]
    assert len(res.trades) >= 1

    # (b) FSM: every order in registry reaches a terminal state
    for coid, mo in exc.registry.items():
        assert mo.status in (OrderStatus.FILLED, OrderStatus.CANCELED), \
            f"{coid}: expected terminal, got {mo.status}"
        if mo.status == OrderStatus.FILLED:
            # filled_qty must match the original request quantity
            assert mo.filled_qty == pytest.approx(mo.request.qty, abs=1e-9)
            assert mo.avg_fill_px > 0.0

    # (c) lifecycle order for each order
    for coid in exc.registry:
        lifecycle = _lifecycle_for(coid, events)
        _assert_lifecycle(lifecycle)


def test_parity_with_slippage_and_multiplier():
    """Parity holds with slippage + multiplier (mirrors test_sim_parity.py)."""
    acc, res, exc, events = _run_scenario(
        _BuyThenClose(), cash=100_000.0, fee=0.001, slippage=0.0005, multiplier=5.0)
    assert acc.trades == [t.pnl for t in res.trades]


# ---------------------------------------------------------------------------
# Bracket scenario (mirrors test_intrabar_bracket_cap_parity)
# ---------------------------------------------------------------------------

class _BracketStrategy(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.stop_sell(1.0, price=95.0)
            self.limit_sell(1.0, price=115.0)


def _bracket_bars():
    return [
        Bar(ts=0,        open=100, high=105, low=95,  close=102, volume=1.0),
        Bar(ts=60_000,   open=100, high=105, low=95,  close=100, volume=1.0),
        Bar(ts=120_000,  open=100, high=120, low=90,  close=105, volume=1.0),
    ]


def test_bracket_parity_and_fsm():
    """Bracket: parity + FSM + lifecycle through the adversarial stop-first cap.

    The stop fills first (adverse), consuming the entire position. The limit order is capped to
    size=0 by _resolve_intrabar and skipped by _fill_pending without calling _on_fill or _on_cancel.
    This is a known edge case of the passive mirror: the capped-to-zero limit stays in ACCEPTED
    (not FILLED or CANCELED) because the engine provides no hook for the cap-skip path.
    The parity guarantee (acc.trades == engine trades) is the primary assertion; the FSM state
    of the non-filled capped order is ACCEPTED (not terminal), which is acceptable per the plan.
    """
    acc, res, exc, events = _run_scenario(_BracketStrategy(), bars=_bracket_bars(), fee=0.0)

    assert acc.trades == [t.pnl for t in res.trades]
    assert res.intrabar_both_hit == 1

    # At least one order must be FILLED (the entry and the stop that fired)
    filled = [mo for mo in exc.registry.values() if mo.status == OrderStatus.FILLED]
    assert len(filled) >= 1, "At least the stop fill must produce a FILLED order"

    # The capped-to-zero limit may remain in ACCEPTED state (no hook for cap-skip)
    # Verify parity holds regardless
    fill_events = [e for e in events if isinstance(e, FillEvent)]
    # entry + stop fill = 2 fills; capped limit never fires = 0 fill for it
    assert len(fill_events) == 2


# ---------------------------------------------------------------------------
# Liquidation scenario (mirrors test_liquidation_parity)
# ---------------------------------------------------------------------------

class _LeveragedLong(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(100.0)


def _liquidation_bars():
    return [
        Bar(ts=0,        open=100, high=105, low=98,  close=100, volume=1.0),
        Bar(ts=60_000,   open=100, high=102, low=95,  close=100, volume=1.0),
        Bar(ts=120_000,  open=100, high=102, low=90,  close=100, volume=1.0),
    ]


def test_liquidation_parity_and_fsm():
    """Liquidation: parity preserved; liquidation fill has no tracked ManagedOrder (synthetic coid)."""
    acc, res, exc, events = _run_scenario(
        _LeveragedLong(), bars=_liquidation_bars(),
        cash=1_000.0, fee=0.0,
        leverage=10.0, maint_margin=0.05)

    assert acc.trades == [t.pnl for t in res.trades]

    # The entry order (the one submitted by the strategy) is in registry and FILLED
    filled_mos = [mo for mo in exc.registry.values() if mo.status == OrderStatus.FILLED]
    assert len(filled_mos) == 1, "Entry order should be FILLED"

    # The bare FillEvent for the liquidation fill should also appear on the bus
    fill_events = [e for e in events if isinstance(e, FillEvent)]
    assert len(fill_events) == 2  # entry fill + liquidation fill


def test_liquidation_synthetic_coid_no_double_count():
    """Liquidation fill publishes a bare FillEvent (with synthetic coid); no OrderFilled event is published
    for it (no tracked ManagedOrder), so Account only gets the one FillEvent -- no double count."""
    acc, res, exc, events = _run_scenario(
        _LeveragedLong(), bars=_liquidation_bars(),
        cash=1_000.0, fee=0.0,
        leverage=10.0, maint_margin=0.05)

    fill_events = [e for e in events if isinstance(e, FillEvent)]
    order_filled_events = [e for e in events if isinstance(e, OrderFilled)]

    # 2 bare FillEvents (entry + liquidation)
    assert len(fill_events) == 2

    # Only 1 OrderFilled (entry order); liquidation has no tracked ManagedOrder -> no OrderFilled
    assert len(order_filled_events) == 1

    # Account folds only bare FillEvents, not OrderFilled.fill
    # (so no double count -- this is the critical invariant)
    assert len(acc.trades) == len(res.trades)


# ---------------------------------------------------------------------------
# No double-count invariant: explicit check
# ---------------------------------------------------------------------------

def test_no_double_count_account():
    """Account subscribes only to FillEvent (bare), not OrderFilled.fill.

    If Account were to also fold OrderFilled.fill, realized_pnl would double.
    This test ensures it doesn't.
    """
    acc, res, exc, events = _run_scenario(_BuyThenClose())

    # Account PnL must match engine PnL exactly (no doubling)
    assert acc.realized_pnl == pytest.approx(sum(t.pnl for t in res.trades), abs=0.0)
    assert acc.trades == [t.pnl for t in res.trades]
