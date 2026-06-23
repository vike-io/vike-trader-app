"""Immutable order/fill/position event value objects."""

import dataclasses

import pytest

from vike_trader_app.exec.events import (
    AccountState,
    FillEvent,
    FundingEvent,
    OrderAccepted,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRequest,
    OrderSubmitted,
    PositionClosed,
    PositionLiquidated,
    PositionOpened,
)


def test_order_request_carries_reserved_contingency_slots_unused():
    req = OrderRequest(client_order_id="c1", venue="binance", symbol="BTCUSDT",
                       side=+1, qty=0.5, order_type="stop", trigger_price=70000.0)
    assert req.order_type == "stop" and req.trigger_price == 70000.0
    # reserved-for-OCO slots exist and default empty (present day-one so OCO is additive later)
    assert req.parent_order_id is None
    assert req.linked_order_ids == ()
    assert req.order_list_id is None
    assert req.contingency_type is None


def test_events_are_frozen_immutable():
    ev = OrderSubmitted(client_order_id="c1", ts=10)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.client_order_id = "c2"  # type: ignore[misc]


def test_fill_event_fields_and_reserved_mark_price():
    f = FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol="BTCUSDT",
                  side=+1, last_qty=0.25, last_px=70010.0, commission=0.07, liquidity_side="taker")
    assert f.last_qty == 0.25 and f.last_px == 70010.0
    assert f.mark_price is None  # reserved for perps; None on spot


def test_fill_carrying_events_hold_the_fill():
    f = FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol="BTCUSDT",
                  side=+1, last_qty=0.25, last_px=70010.0)
    assert OrderPartiallyFilled(client_order_id="c1", fill=f).fill is f
    assert OrderFilled(client_order_id="c1", fill=f).fill is f


def test_accepted_carries_venue_order_id():
    assert OrderAccepted(client_order_id="c1", venue_order_id="V99").venue_order_id == "V99"


def test_position_events_carry_position_side_for_hedge_mode():
    # position_side present day-one so hedge-mode perps are additive (stress-test #3 hardening)
    op = PositionOpened(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                        qty=0.5, avg_px=70000.0)
    assert op.position_side == "BOTH"
    assert PositionClosed(venue="binance", symbol="BTCUSDT", position_side="BOTH").realized_pnl == 0.0


def test_account_state_balances_are_immutable_tuple():
    acct = AccountState(venue="binance", balances=(("USDT", 1000.0), ("BTC", 0.5)))
    assert dict(acct.balances)["USDT"] == 1000.0


def test_reserved_perp_events_exist_but_are_not_wired():
    # defined so Phase-5 perps populate-the-slot rather than migrate the taxonomy
    assert FundingEvent(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                        funding_rate=0.0001, amount=-0.12).amount == -0.12
    assert PositionLiquidated(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                              qty=0.5, liq_price=61000.0).liq_price == 61000.0
