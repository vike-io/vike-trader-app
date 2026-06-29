"""Slice 5g-2: hedge-engine dual-side LONG/SHORT keying at the LiveOmsHub level.

PRIMARY + merge-gating offline proof. Four invariants:
  1. (CORE RED→GREEN) A per-side PositionLiquidated resolves the RIGHT owning order's FSM
     (long liq -> long order; short -> short); the other leg's order stays ACCEPTED.
  2. One-way (position_side='BOTH') resolution is BYTE-IDENTICAL to today: the most-recent
     non-terminal order on the symbol is advanced, regardless of side.
  3. Dual-side TRACKING via bare FillEvents: a LONG fill + SHORT fill on the same symbol produce
     two independent Account positions, and the _position_size(side_param) reads each leg
     correctly; total_exposure() sums abs() of both legs at mark (never nets).
  4. total_exposure() parity: one-way single-leg == abs(size)*mark, 0.0 without a mark.

No network: scripted bare FillEvents + PositionLiquidated (no mapper dual-publish path), plus
binance mapper only for the tracking test. EventBus is synchronous/FIFO so no QThread/teardown
concern.
"""
from __future__ import annotations

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent, OrderRequest, PositionLiquidated
from vike_trader_app.exec.live_oms import LiveOmsHub
from vike_trader_app.exec.order import ManagedOrder, OrderStatus
from vike_trader_app.exec.risk import RiskGate, RiskLimits

from vike_trader_app.exec.binance.perp_mapper import map_binance_perp


class _SpyClient:
    def submit(self, request): pass
    def detach(self): pass


def _hub(venue="binance", symbol="BTCUSDT"):
    return LiveOmsHub(bus=EventBus(), account=Account(venue=venue), gate=RiskGate(RiskLimits()),
                      client=_SpyClient(), venue=venue, symbol=symbol)


def _seed_order(hub, coid, side, venue="binance", symbol="BTCUSDT", qty=1.0, px=100.0):
    """Register a live (ACCEPTED) order in the registry — no FillEvent, so it STAYS ACCEPTED.

    Bare registry seeding (not through a mapper) avoids the dual-publish trap: a mapper emits
    both FillEvent and OrderFilled; the OrderFilled wrap advances the ACCEPTED order to FILLED
    (terminal), and _coid_for_position skips terminal orders -> the test proves nothing.
    Mirroring test_live_oms_perp_5e.py:75-80 _seed_long but WITHOUT the bare FillEvent here
    so the order remains ACCEPTED and the liquidation test is genuine.
    """
    req = OrderRequest(client_order_id=coid, venue=venue, symbol=symbol,
                       side=side, qty=qty, order_type="limit", price=px)
    hub.registry[coid] = ManagedOrder(request=req, status=OrderStatus.ACCEPTED)


def _seed_with_fill(hub, coid, side, qty, px, position_side, tid, venue="binance", symbol="BTCUSDT"):
    """Seed an order AND a bare FillEvent so Account has a position (order stays ACCEPTED)."""
    _seed_order(hub, coid, side, venue=venue, symbol=symbol, qty=qty, px=px)
    hub.bus.publish(FillEvent(
        trade_id=tid, client_order_id=coid, venue=venue, symbol=symbol,
        side=side, last_qty=qty, last_px=px, position_side=position_side,
    ))


# ---------------------------------------------------------------------------
# 1. CORE: hedge dual-side FSM owner-resolution (the main RED→GREEN invariant)
# ---------------------------------------------------------------------------

def test_hedge_long_liquidation_advances_only_long_order():
    """THE CORE TEST.

    Before the fix: _coid_for_position uses symbol-only predicate; in reversed() dict order
    short-o is the most-recently inserted live order, so it would be (wrongly) advanced to
    LIQUIDATED and long-o would stay ACCEPTED — demonstrably wrong.

    After the fix: the LONG liq correctly identifies long-o (side=+1 -> 'LONG') and leaves
    short-o untouched.
    """
    hub = _hub()
    _seed_with_fill(hub, "long-o", side=+1, qty=1.0, px=100.0, position_side="LONG", tid="f1")
    _seed_with_fill(hub, "short-o", side=-1, qty=1.0, px=200.0, position_side="SHORT", tid="f2")

    # Liquidate ONLY the LONG leg.
    hub.bus.publish(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="LONG",
        qty=1.0, liq_price=60.0, fee=0.5, trade_id="liqL",
    ))

    # LONG order FSM advanced; SHORT order stays ACCEPTED.
    assert hub.registry["long-o"].status is OrderStatus.LIQUIDATED
    assert hub.registry["short-o"].status is OrderStatus.ACCEPTED
    # Account: LONG leg flat; SHORT leg intact.
    assert hub.account.positions[("binance", "BTCUSDT", "LONG")]["size"] == 0.0
    assert hub.account.positions[("binance", "BTCUSDT", "SHORT")]["size"] == -1.0
    assert hub.account.realized_pnl == -40.0   # (60-100)*1
    assert hub.account.balance == -0.5


def test_hedge_short_liquidation_advances_only_short_order():
    """Mirror of the long case — the SHORT liq advances short-o, long-o stays ACCEPTED."""
    hub = _hub()
    _seed_with_fill(hub, "long-o", side=+1, qty=1.0, px=100.0, position_side="LONG", tid="f1")
    _seed_with_fill(hub, "short-o", side=-1, qty=1.0, px=200.0, position_side="SHORT", tid="f2")

    hub.bus.publish(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="SHORT",
        qty=1.0, liq_price=260.0, fee=0.5, trade_id="liqS",
    ))

    assert hub.registry["short-o"].status is OrderStatus.LIQUIDATED
    assert hub.registry["long-o"].status is OrderStatus.ACCEPTED
    assert hub.account.positions[("binance", "BTCUSDT", "SHORT")]["size"] == 0.0
    assert hub.account.positions[("binance", "BTCUSDT", "LONG")]["size"] == 1.0
    assert hub.account.realized_pnl == -60.0   # (260-200)*-1
    assert hub.account.balance == -0.5


# ---------------------------------------------------------------------------
# 2. ONE-WAY BYTE-EQUIVALENCE: BOTH path unchanged
# ---------------------------------------------------------------------------

def test_one_way_both_resolution_is_unchanged():
    """BOTH: the most-recent non-terminal order on the symbol wins (pre-5g-2 behavior)."""
    hub = _hub()
    _seed_order(hub, "old-o", side=+1)
    _seed_order(hub, "new-o", side=+1)
    # Bare FillEvent on new-o (BOTH) — seeding an Account position for the liquidation to close.
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="new-o", venue="binance",
        symbol="BTCUSDT", side=+1, last_qty=2.0, last_px=100.0,
    ))  # default position_side='BOTH'

    hub.bus.publish(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=2.0, liq_price=60.0, fee=0.5, trade_id="liqB",
    ))

    # Most-recent live order wins (new-o), unchanged from the old symbol-only reversed() scan.
    assert hub.registry["new-o"].status is OrderStatus.LIQUIDATED
    assert hub.registry["old-o"].status is OrderStatus.ACCEPTED
    assert hub.account.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0


# ---------------------------------------------------------------------------
# 3. DUAL-SIDE TRACKING via bare FillEvents (position_size + total_exposure)
# ---------------------------------------------------------------------------

def test_dual_side_tracking_via_bare_fill_events():
    """A LONG fill + SHORT fill on the same symbol produce two independent Account positions."""
    hub = _hub()
    # Bare FillEvents with explicit position_side — no mapper needed.
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="c1", venue="binance",
        symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=100.0, position_side="LONG",
    ))
    hub.bus.publish(FillEvent(
        trade_id="f2", client_order_id="c2", venue="binance",
        symbol="BTCUSDT", side=-1, last_qty=1.0, last_px=200.0, position_side="SHORT",
    ))

    # Two independent positions, no merged BOTH key.
    assert hub.account.positions[("binance", "BTCUSDT", "LONG")]["size"] == 1.0
    assert hub.account.positions[("binance", "BTCUSDT", "SHORT")]["size"] == -1.0
    assert ("binance", "BTCUSDT", "BOTH") not in hub.account.positions


def test_dual_side_tracking_via_binance_mapper():
    """Same as above but routed through the real Binance perp mapper (already emits LONG/SHORT)."""
    hub = _hub()

    def _binance_fill(side_str, qty, px, pside, coid, tid):
        return {"e": "ORDER_TRADE_UPDATE", "T": 1, "o": {
            "s": "BTCUSDT", "c": coid, "x": "TRADE", "X": "FILLED", "S": side_str,
            "ps": pside, "l": str(qty), "L": str(px), "n": "0.0", "t": tid}}

    for ev in map_binance_perp(_binance_fill("BUY", 1.0, 100.0, "LONG", "long-o", 1),
                                venue="binance", symbol="BTCUSDT"):
        hub.bus.publish(ev)
    for ev in map_binance_perp(_binance_fill("SELL", 1.0, 200.0, "SHORT", "short-o", 2),
                                venue="binance", symbol="BTCUSDT"):
        hub.bus.publish(ev)

    assert hub.account.positions[("binance", "BTCUSDT", "LONG")]["size"] == 1.0
    assert hub.account.positions[("binance", "BTCUSDT", "SHORT")]["size"] == -1.0
    assert ("binance", "BTCUSDT", "BOTH") not in hub.account.positions


# ---------------------------------------------------------------------------
# 4. _position_size side-param + total_exposure
# ---------------------------------------------------------------------------

def test_position_size_default_both_and_side_param():
    """Default 'BOTH' returns 0.0 in hedge mode (no BOTH leg); side-param reads each leg."""
    hub = _hub()
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="c1", venue="binance",
        symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=100.0, position_side="LONG",
    ))
    hub.bus.publish(FillEvent(
        trade_id="f2", client_order_id="c2", venue="binance",
        symbol="BTCUSDT", side=-1, last_qty=2.0, last_px=200.0, position_side="SHORT",
    ))

    assert hub._position_size() == 0.0          # no BOTH leg in hedge mode -> 0.0 (byte-equiv)
    assert hub._position_size("LONG") == 1.0
    assert hub._position_size("SHORT") == -2.0


def test_position_size_one_way_unchanged():
    """One-way BOTH leg: default call returns the same value as pre-5g-2."""
    hub = _hub()
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="c", venue="binance",
        symbol="BTCUSDT", side=+1, last_qty=3.0, last_px=100.0,
    ))  # default position_side='BOTH'

    assert hub._position_size() == 3.0          # identical to pre-5g-2 single-leg read


def test_total_exposure_sums_both_legs_at_mark():
    """total_exposure() is GROSS (abs sum), never netted: 1.0L + 2.0S at mark 150 = 450."""
    hub = _hub()
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="c1", venue="binance",
        symbol="BTCUSDT", side=+1, last_qty=1.0, last_px=100.0, position_side="LONG",
    ))
    hub.bus.publish(FillEvent(
        trade_id="f2", client_order_id="c2", venue="binance",
        symbol="BTCUSDT", side=-1, last_qty=2.0, last_px=200.0, position_side="SHORT",
    ))
    hub.account.set_mark("binance", "BTCUSDT", 150.0)

    # GROSS, not net: (|1| + |-2|) * 150 = 450; a netting bug would give (1-2)*150 = 150.
    assert hub.total_exposure() == 450.0


def test_total_exposure_one_way_equals_single_leg_notional():
    """One-way: total_exposure() == abs(size)*mark, identical to a single-leg today."""
    hub = _hub()
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="c", venue="binance",
        symbol="BTCUSDT", side=-1, last_qty=2.0, last_px=100.0,
    ))  # default BOTH short
    hub.account.set_mark("binance", "BTCUSDT", 150.0)

    assert hub.total_exposure() == 300.0        # |-2| * 150


def test_total_exposure_zero_without_mark():
    """total_exposure() returns 0.0 when no mark has been recorded yet."""
    hub = _hub()
    hub.bus.publish(FillEvent(
        trade_id="f1", client_order_id="c", venue="binance",
        symbol="BTCUSDT", side=+1, last_qty=2.0, last_px=100.0,
    ))

    assert hub.total_exposure() == 0.0          # no mark recorded
