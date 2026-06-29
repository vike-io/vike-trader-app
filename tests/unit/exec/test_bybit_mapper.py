"""Pure Bybit private-WS mapper: each topic/status combo -> the right vike event(s).

execution topic: Trade rows -> [FillEvent, OrderPartiallyFilled|OrderFilled] (dual-publish).
order topic: status rows -> lifecycle events only (no fill fold, no double-fold).
op-only frames (pong/auth/subscribe) -> [].

SELL sign: side maps +1 for 'Buy', -1 for 'Sell'; last_qty is always positive.
leavesQty terminal: OrderFilled when cumExecQty >= orderQty OR leavesQty parses to exactly 0.
order-topic 'New' -> OrderAccepted (LiveOmsHub swallows the redundant transition from REST publish).
order-topic 'Filled'/'PartiallyFilled' -> [] (already covered by execution topic, no double-fold).
"""

from __future__ import annotations

import pytest

from vike_trader_app.exec.bybit.mapper import map_bybit_private, map_execution, map_order
from vike_trader_app.exec.events import (
    AccountState,
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _exec_row(**kw) -> dict:
    base = {
        "execId": "exec-111",
        "orderLinkId": "coid-1",
        "orderId": "ord-42",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execType": "Trade",
        "execQty": "1.0",
        "execPrice": "65000",
        "execFee": "0.01",
        "leavesQty": "0",
        "orderQty": "1.0",
        "cumExecQty": "1.0",
        "isMaker": False,
        "execTime": "1700000000000",
    }
    base.update(kw)
    return base


def _order_row(**kw) -> dict:
    base = {
        "orderId": "ord-9",
        "orderLinkId": "coid-9",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderStatus": "New",
        "cancelType": "",
        "rejectReason": "EC_NoError",
        "createdTime": "1700000000000",
        "updatedTime": "1700000000000",
    }
    base.update(kw)
    return base


def _frame(topic: str, rows: list[dict]) -> dict:
    return {"topic": topic, "data": rows}


# ---------------------------------------------------------------------------
# execution topic — fill tests
# ---------------------------------------------------------------------------

def test_execution_full_fill_emits_fill_and_orderfilled():
    """A Trade row with leavesQty='0' should emit [FillEvent, OrderFilled]."""
    row = _exec_row(leavesQty="0", isMaker=False, execFee="0.01")
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 2
    fe = out[0]
    assert isinstance(fe, FillEvent)
    assert fe.trade_id == "exec-111"
    assert fe.client_order_id == "coid-1"
    assert fe.side == +1
    assert fe.last_qty == 1.0
    assert fe.last_px == 65000.0
    assert fe.commission == 0.01
    assert fe.liquidity_side == "taker"
    wrapper = out[1]
    assert isinstance(wrapper, OrderFilled)
    assert wrapper.fill is fe


def test_execution_partial_emits_orderpartiallyfilled():
    """A Trade row with leavesQty='0.5' should emit [FillEvent, OrderPartiallyFilled]."""
    row = _exec_row(leavesQty="0.5", execQty="0.5", cumExecQty="0.5", orderQty="1.0")
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 2
    assert isinstance(out[0], FillEvent)
    assert isinstance(out[1], OrderPartiallyFilled)
    assert out[1].fill is out[0]


def test_execution_leaves_qty_string_zero_with_dust_emits_filled():
    """leavesQty='0.00000000' (string dust) should still map to OrderFilled."""
    row = _exec_row(leavesQty="0.00000000", cumExecQty="1.0", orderQty="1.0")
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert isinstance(out[1], OrderFilled)


def test_execution_cumeexecqty_ge_orderqty_emits_filled():
    """When cumExecQty >= orderQty the order is terminal -> OrderFilled even if leavesQty is positive."""
    row = _exec_row(leavesQty="0.00001", cumExecQty="1.0", orderQty="1.0")
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert isinstance(out[1], OrderFilled)


def test_execution_sell_side_maps_negative_one():
    """side='Sell' must produce FillEvent.side == -1; last_qty is always positive."""
    row = _exec_row(side="Sell", execQty="0.5", leavesQty="0.5", cumExecQty="0.5", orderQty="1.0")
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert isinstance(out[0], FillEvent)
    fe: FillEvent = out[0]
    assert fe.side == -1
    assert fe.last_qty == 0.5  # always positive


def test_execution_non_trade_exectype_skipped():
    """execType != 'Trade' (e.g. Funding, AdlTrade, BustTrade) must return []."""
    for exec_type in ("Funding", "AdlTrade", "BustTrade", "SettleFundingFee"):
        row = _exec_row(execType=exec_type)
        assert map_execution(row, venue="bybit", symbol="BTCUSDT") == [], \
            f"Expected [] for execType={exec_type!r}"


def test_execution_empty_orderlinkid_still_builds_fill():
    """orderLinkId='' is allowed: FillEvent.client_order_id == '' (registry lookup no-ops downstream)."""
    row = _exec_row(orderLinkId="")
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert isinstance(out[0], FillEvent)
    assert out[0].client_order_id == ""


def test_execution_maker_sets_liquidity_side_maker():
    """isMaker=True must set liquidity_side='maker'."""
    row = _exec_row(isMaker=True)
    out = map_execution(row, venue="bybit", symbol="BTCUSDT")
    assert out[0].liquidity_side == "maker"


# ---------------------------------------------------------------------------
# order topic — lifecycle tests
# ---------------------------------------------------------------------------

def test_order_new_emits_accepted_with_venue_id():
    """orderStatus='New' -> [OrderAccepted(venue_order_id=orderId)]."""
    row = _order_row(orderStatus="New", orderId="9")
    out = map_order(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderAccepted)
    assert out[0].venue_order_id == "9"
    # Note: LiveOmsHub swallows the redundant OrderAccepted transition (REST already published one)


def test_order_cancelled_emits_canceled():
    """orderStatus='Cancelled' -> [OrderCanceled(reason=cancelType)]."""
    row = _order_row(orderStatus="Cancelled", cancelType="CancelByUser")
    out = map_order(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderCanceled)
    assert out[0].reason == "CancelByUser"


def test_order_partially_filled_canceled_emits_canceled():
    """orderStatus='PartiallyFilledCanceled' -> [OrderCanceled(reason=cancelType)]."""
    row = _order_row(orderStatus="PartiallyFilledCanceled", cancelType="CancelByUser")
    out = map_order(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderCanceled)
    assert out[0].reason == "CancelByUser"


def test_order_rejected_emits_rejected():
    """orderStatus='Rejected' -> [OrderRejected(reason=rejectReason)]."""
    row = _order_row(orderStatus="Rejected", rejectReason="EC_PerIpOrderFreqExceeded")
    out = map_order(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderRejected)
    assert out[0].reason == "EC_PerIpOrderFreqExceeded"


def test_order_deactivated_emits_expired():
    """orderStatus='Deactivated' -> [OrderExpired]."""
    row = _order_row(orderStatus="Deactivated")
    out = map_order(row, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 1
    assert isinstance(out[0], OrderExpired)


def test_order_filled_status_emits_nothing():
    """orderStatus='Filled' -> [] (execution topic covers this; no double-fold)."""
    row = _order_row(orderStatus="Filled")
    assert map_order(row, venue="bybit", symbol="BTCUSDT") == []


def test_order_partially_filled_status_emits_nothing():
    """orderStatus='PartiallyFilled' -> [] (execution topic covers this; no double-fold)."""
    row = _order_row(orderStatus="PartiallyFilled")
    assert map_order(row, venue="bybit", symbol="BTCUSDT") == []


# ---------------------------------------------------------------------------
# frame-level dispatch tests
# ---------------------------------------------------------------------------

def test_map_frame_iterates_execution_array():
    """A frame with two Trade rows must produce 4 events (2 fills + 2 wrappers)."""
    row1 = _exec_row(execId="exec-1", orderLinkId="coid-1", leavesQty="0")
    row2 = _exec_row(execId="exec-2", orderLinkId="coid-2", leavesQty="0")
    frame = _frame("execution", [row1, row2])
    out = map_bybit_private(frame, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 4
    fills = [e for e in out if isinstance(e, FillEvent)]
    wrappers = [e for e in out if isinstance(e, OrderFilled)]
    assert len(fills) == 2
    assert len(wrappers) == 2


def test_map_frame_order_topic_dispatches():
    """An order topic frame iterates the data array and emits lifecycle events."""
    row1 = _order_row(orderStatus="New", orderId="10")
    row2 = _order_row(orderStatus="Cancelled", cancelType="CancelByUser", orderId="11")
    frame = _frame("order", [row1, row2])
    out = map_bybit_private(frame, venue="bybit", symbol="BTCUSDT")
    assert len(out) == 2
    assert isinstance(out[0], OrderAccepted)
    assert isinstance(out[1], OrderCanceled)


def test_map_frame_ack_and_pong_ignored():
    """op-only frames (pong, auth ack, subscribe ack) must return []."""
    pong = {"op": "pong", "args": ["1"]}
    auth_ack = {"success": True, "op": "auth"}
    sub_ack = {"success": True, "op": "subscribe"}
    for frame in (pong, auth_ack, sub_ack):
        assert map_bybit_private(frame, venue="bybit", symbol="") == [], \
            f"Expected [] for frame={frame!r}"


def test_map_frame_unknown_topic_ignored():
    """Unrecognised topic frames must return []; wallet topic now handled separately."""
    frame = {"topic": "position", "data": [{"something": "1000"}]}
    assert map_bybit_private(frame, venue="bybit", symbol="") == []


# ---------------------------------------------------------------------------
# wallet topic -> AccountState (Bybit unified account, same as perp)
# ---------------------------------------------------------------------------

def _wallet_frame(coins: list[dict], creation_time: int = 1700000000000) -> dict:
    """Bybit V5 wallet topic frame with one account entry."""
    return {
        "topic": "wallet",
        "creationTime": creation_time,
        "data": [{"coin": coins}],
    }


def test_wallet_topic_emits_account_state():
    """wallet topic with coin rows -> AccountState(venue, balances, ts) using walletBalance (TOTAL)."""
    frame = _wallet_frame([
        {"coin": "USDT", "walletBalance": "1234.56"},
        {"coin": "BTC", "walletBalance": "0.5"},
    ], creation_time=1700000000000)
    out = map_bybit_private(frame, venue="bybit", symbol="")
    acct_states = [e for e in out if isinstance(e, AccountState)]
    assert len(acct_states) == 1
    state = acct_states[0]
    assert state.venue == "bybit"
    assert state.ts == 1700000000000
    balances_dict = dict(state.balances)
    assert balances_dict["USDT"] == pytest.approx(1234.56)
    assert balances_dict["BTC"] == pytest.approx(0.5)


def test_wallet_topic_empty_coin_list_emits_nothing():
    """wallet topic with empty coin list -> []."""
    frame = _wallet_frame([])
    out = map_bybit_private(frame, venue="bybit", symbol="")
    assert not any(isinstance(e, AccountState) for e in out)


def test_wallet_topic_malformed_balance_skipped_default_safe():
    """A coin row with bad walletBalance is skipped; valid rows still emit AccountState."""
    frame = _wallet_frame([
        {"coin": "USDT", "walletBalance": "999.0"},
        {"coin": "ETH", "walletBalance": "not_a_number"},
    ])
    out = map_bybit_private(frame, venue="bybit", symbol="")
    acct_states = [e for e in out if isinstance(e, AccountState)]
    assert len(acct_states) == 1
    balances_dict = dict(acct_states[0].balances)
    assert "USDT" in balances_dict
    assert "ETH" not in balances_dict


def test_wallet_topic_does_not_affect_execution_output():
    """An execution frame still emits [FillEvent, OrderFilled]; no AccountState."""
    row = _exec_row()
    frame = _frame("execution", [row])
    out = map_bybit_private(frame, venue="bybit", symbol="BTCUSDT")
    assert any(isinstance(e, FillEvent) for e in out)
    assert not any(isinstance(e, AccountState) for e in out)
