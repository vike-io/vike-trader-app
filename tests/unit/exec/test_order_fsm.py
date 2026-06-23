"""OrderStatus FSM: legal transitions advance, illegal ones raise, fills accumulate."""

import pytest

from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRequest,
    OrderSubmitted,
    OrderTriggered,
)
from vike_trader_app.exec.order import (
    InvalidOrderTransition,
    ManagedOrder,
    OrderStatus,
)


def _order(qty=1.0, order_type="stop"):
    return ManagedOrder(OrderRequest(client_order_id="c1", venue="binance", symbol="BTCUSDT",
                                     side=+1, qty=qty, order_type=order_type, trigger_price=70000.0))


def _fill(qty, px, tid="t1"):
    return FillEvent(trade_id=tid, client_order_id="c1", venue="binance", symbol="BTCUSDT",
                     side=+1, last_qty=qty, last_px=px)


def test_happy_path_stop_submit_accept_trigger_fill():
    o = _order()
    assert o.status is OrderStatus.INITIALIZED
    o.apply(OrderSubmitted("c1"));            assert o.status is OrderStatus.SUBMITTED
    o.apply(OrderAccepted("c1", "V1"));       assert o.status is OrderStatus.ACCEPTED
    assert o.venue_order_id == "V1"
    o.apply(OrderTriggered("c1"));            assert o.status is OrderStatus.TRIGGERED
    o.apply(OrderFilled("c1", _fill(1.0, 70010.0)))
    assert o.status is OrderStatus.FILLED
    assert o.filled_qty == 1.0 and o.avg_fill_px == 70010.0


def test_partial_fills_accumulate_qty_and_vwap():
    o = _order(qty=1.0)
    o.apply(OrderSubmitted("c1")); o.apply(OrderAccepted("c1"))
    o.apply(OrderPartiallyFilled("c1", _fill(0.4, 100.0, "t1")))
    assert o.status is OrderStatus.PARTIALLY_FILLED and o.filled_qty == 0.4
    o.apply(OrderFilled("c1", _fill(0.6, 110.0, "t2")))
    assert o.status is OrderStatus.FILLED and o.filled_qty == 1.0
    assert o.avg_fill_px == pytest.approx((0.4 * 100.0 + 0.6 * 110.0) / 1.0)


def test_illegal_transition_raises():
    o = _order()
    # cannot accept before submit
    with pytest.raises(InvalidOrderTransition):
        o.apply(OrderAccepted("c1"))


def test_partially_filled_leg_can_be_canceled_keeping_filled_qty():
    # stress-test #2 hardening: a server-side OCO sibling-cancel of a partially-filled leg must
    # NOT raise; remaining qty cancels, filled qty stays.
    o = _order(qty=1.0)
    o.apply(OrderSubmitted("c1")); o.apply(OrderAccepted("c1"))
    o.apply(OrderPartiallyFilled("c1", _fill(0.4, 100.0)))
    o.apply(OrderCanceled("c1", reason="oco-sibling"))
    assert o.status is OrderStatus.CANCELED and o.filled_qty == 0.4


def test_triggered_leg_can_be_canceled():
    # a fired stop (TRIGGERED) can still be canceled — OCO sibling-cancel racing the fill
    o = _order()
    o.apply(OrderSubmitted("c1")); o.apply(OrderAccepted("c1"))
    o.apply(OrderTriggered("c1"))
    o.apply(OrderCanceled("c1", reason="oco-sibling"))
    assert o.status is OrderStatus.CANCELED


def test_accepted_can_cancel_directly():
    o = _order()
    o.apply(OrderSubmitted("c1")); o.apply(OrderAccepted("c1"))
    o.apply(OrderCanceled("c1"))
    assert o.status is OrderStatus.CANCELED


def test_reserved_terminal_states_exist():
    # LIQUIDATED/EMULATED/RELEASED reserved for perps/emulated-conditional (Phase 5)
    assert {"LIQUIDATED", "EMULATED", "RELEASED"} <= set(OrderStatus.__members__)
