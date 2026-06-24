"""Pure-mapper tests for exec/okx/mapper.py (no socket, no Qt).

All scripted frames use real field names from the OKX orders-channel WebSocket spec.
The INTEGRATION test (test_filled_row_advances_managed_order_fsm) proves the
OrderFilled wrap drives the ManagedOrder FSM offline so the bug cannot hide behind
a green mapper and only fail the network smoke.
"""
from __future__ import annotations

import pytest

from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _order_row(**kw) -> dict:
    """Return a baseline orders-channel row, overridable via kwargs."""
    row = {
        "instId": "BTC-USDT",
        "ordId": "ord-1",
        "clOrdId": "coid-1",
        "side": "buy",
        "ordType": "limit",
        "sz": "0.001",
        "px": "50000",
        "state": "filled",
        "fillSz": "0.001",
        "fillPx": "50000",
        "fillFee": "-0.05",
        "fillTime": "1700000000000",
        "tradeId": "T1",
        "execType": "T",
        "accFillSz": "0.001",
        "code": "",
        "msg": "",
        "cancelSource": "",
        "uTime": "1700000000000",
    }
    row.update(kw)
    return row


def _frame(rows: list[dict]) -> dict:
    """Wrap rows in a minimal orders-channel frame."""
    return {
        "arg": {"channel": "orders", "instType": "SPOT"},
        "data": rows,
    }


# ---------------------------------------------------------------------------
# Import the mapper (will fail RED until mapper.py exists)
# ---------------------------------------------------------------------------

from vike_trader_app.exec.okx.mapper import map_okx_order, map_okx_private  # noqa: E402


# ---------------------------------------------------------------------------
# Fill tests
# ---------------------------------------------------------------------------

def test_buy_fill_full():
    """Full buy fill: [FillEvent, OrderFilled] with correct field values including ABS commission."""
    row = _order_row(
        side="buy",
        state="filled",
        fillSz="0.001",
        fillPx="50000",
        fillFee="-0.05",
        tradeId="T1",
        clOrdId="coid-1",
        instId="BTC-USDT",
        execType="T",
        accFillSz="0.001",
        sz="0.001",
        fillTime="1700000000000",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderFilled)
    # FillEvent fields
    assert fill.trade_id == "T1"
    assert fill.client_order_id == "coid-1"
    assert fill.symbol == "BTC-USDT"
    assert fill.side == +1
    assert fill.last_qty == 0.001
    assert fill.last_px == 50000.0
    assert fill.commission == 0.05           # ABS of negative fillFee
    assert fill.liquidity_side == "taker"   # execType='T' -> taker
    # Wrap correctness: client_order_id matches; .fill IS the same FillEvent object
    assert wrap.client_order_id == "coid-1"
    assert wrap.fill is fill                 # identity — drives the FSM without double-fold


def test_sell_fill():
    """Sell, maker fill -> side==-1, liquidity_side=='maker', OrderFilled."""
    row = _order_row(
        side="sell",
        state="filled",
        fillSz="0.001",
        fillPx="50000",
        fillFee="-0.04",
        tradeId="T1s",
        execType="M",
        accFillSz="0.001",
        sz="0.001",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderFilled)
    assert fill.side == -1
    assert fill.liquidity_side == "maker"


def test_partial_fill():
    """Partial fill: accFillSz < sz and state != 'filled' -> [FillEvent, OrderPartiallyFilled]."""
    row = _order_row(
        state="partially_filled",
        fillSz="0.0004",
        fillPx="50000",
        fillFee="-0.02",
        tradeId="T2",
        accFillSz="0.0004",
        sz="0.001",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderPartiallyFilled)
    assert fill.last_qty == 0.0004
    assert fill.last_qty != fill.last_qty * 2  # sanity: not doubled


def test_terminal_filled_emits_fill_and_orderfilled():
    """state='filled' always emits [FillEvent, OrderFilled]; the wrap drives the FSM to FILLED."""
    row = _order_row(
        state="filled",
        fillSz="0.001",
        sz="0.001",
        tradeId="T3",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderFilled)
    assert wrap.fill is fill


def test_accfillsz_ge_sz_is_filled_even_if_state_lags():
    """accFillSz >= sz -> OrderFilled even if state still reads 'partially_filled'."""
    row = _order_row(
        state="partially_filled",   # lagging state from OKX
        fillSz="0.0006",
        accFillSz="0.001",          # equals sz -> fully filled
        sz="0.001",
        tradeId="T3b",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderFilled)   # NOT OrderPartiallyFilled — accFillSz fallback


# ---------------------------------------------------------------------------
# Lifecycle-only (no fill) tests
# ---------------------------------------------------------------------------

def test_live_lifecycle_only_no_fill():
    """state='live', no fillSz/tradeId -> [OrderAccepted] with correct venue_order_id, NO FillEvent."""
    row = _order_row(
        state="live",
        fillSz="0",
        fillPx="",
        tradeId="",
        ordId="ord-9",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 1
    assert isinstance(result[0], OrderAccepted)
    assert result[0].venue_order_id == "ord-9"
    assert not any(isinstance(e, FillEvent) for e in result)


def test_canceled_lifecycle():
    """state='canceled', no fill -> exactly [OrderCanceled], zero FillEvent."""
    row = _order_row(
        state="canceled",
        fillSz="0",
        tradeId="",
        cancelSource="user",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 1
    assert isinstance(result[0], OrderCanceled)
    assert not any(isinstance(e, FillEvent) for e in result)


def test_mmp_canceled_lifecycle():
    """state='mmp_canceled' -> [OrderCanceled], no FillEvent."""
    row = _order_row(
        state="mmp_canceled",
        fillSz="0",
        tradeId="",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 1
    assert isinstance(result[0], OrderCanceled)


def test_per_row_error_maps_rejected():
    """per-row code != '0' -> [OrderRejected(reason=msg)], NO FillEvent."""
    row = _order_row(
        code="51000",
        msg="param error",
        state="live",
        fillSz="0",
        tradeId="",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert any(isinstance(e, OrderRejected) for e in result)
    assert not any(isinstance(e, FillEvent) for e in result)
    rejected = next(e for e in result if isinstance(e, OrderRejected))
    assert rejected.reason == "param error"


def test_filled_state_no_fill_sz_returns_empty():
    """state='filled' but no fillSz/tradeId (snapshot/dup) -> [] (the fill already folded)."""
    row = _order_row(
        state="filled",
        fillSz="0",
        tradeId="",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert result == []


def test_partially_filled_state_no_fill_sz_returns_empty():
    """state='partially_filled' but no fillSz/tradeId -> []."""
    row = _order_row(
        state="partially_filled",
        fillSz="0",
        tradeId="",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert result == []


# ---------------------------------------------------------------------------
# Symbol pass-through test
# ---------------------------------------------------------------------------

def test_non_our_symbol_row_still_maps():
    """Mapper does NOT filter by symbol — hub guards symbol. instId in event must be 'ETH-USDT'."""
    row = _order_row(
        instId="ETH-USDT",
        state="filled",
        fillSz="0.5",
        fillPx="3000",
        fillFee="-0.15",
        tradeId="T4",
        accFillSz="0.5",
        sz="0.5",
    )
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    fill, _ = result
    assert fill.symbol == "ETH-USDT"   # venue's instId, not the hub's default symbol


# ---------------------------------------------------------------------------
# map_okx_private dispatch tests
# ---------------------------------------------------------------------------

def test_login_ack_returns_empty():
    assert map_okx_private({"event": "login", "code": "0"}) == []


def test_subscribe_ack_returns_empty():
    assert map_okx_private({"event": "subscribe", "arg": {"channel": "orders"}}) == []


def test_error_ack_returns_empty():
    assert map_okx_private({"event": "error", "code": "60009", "msg": "x"}) == []


def test_pong_string_returns_empty():
    assert map_okx_private("pong") == []
    assert map_okx_private("ping") == []


def test_unknown_channel_returns_empty():
    assert map_okx_private({"arg": {"channel": "account"}, "data": [{}]}) == []


def test_orders_channel_dispatches_to_map_okx_order():
    """map_okx_private dispatches orders channel and returns events from map_okx_order."""
    row = _order_row(state="filled", fillSz="0.001", tradeId="T5", accFillSz="0.001", sz="0.001")
    frame = _frame([row])
    result = map_okx_private(frame, venue="okx", symbol="BTC-USDT")
    assert len(result) == 2
    assert isinstance(result[0], FillEvent)
    assert isinstance(result[1], OrderFilled)


def test_commission_abs_sign():
    """fillFee is negative from OKX; FillEvent.commission must be positive (abs value)."""
    row = _order_row(fillFee="-1.23", fillSz="0.001", tradeId="Tabc", accFillSz="0.001", sz="0.001")
    result = map_okx_order(row, venue="okx", symbol="BTC-USDT")
    fill = result[0]
    assert fill.commission == pytest.approx(1.23)


# ---------------------------------------------------------------------------
# INTEGRATION test — proves the OrderFilled wrap drives the ManagedOrder FSM
# (offline, no network required)
# ---------------------------------------------------------------------------

def test_filled_row_advances_managed_order_fsm():
    """The [FillEvent, OrderFilled] pair must advance ManagedOrder to FILLED + set filled_qty.

    Without the OrderFilled wrap, filled_qty stays 0.0 and status stays ACCEPTED — this
    is the exact bug the scope-review caught (bare FillEvent only folds Account, not FSM).
    Mirrors test_live_oms.py construction with _SyncLifecycleClient so the order reaches
    ACCEPTED before the WS fill arrives.
    """
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.events import OrderRequest, OrderSubmitted
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.order import OrderStatus
    from vike_trader_app.exec.risk import RiskGate, RiskLimits

    # Build a sync lifecycle client (mirrors test_live_oms._SyncLifecycleClient)
    class _SyncClient:
        def __init__(self, bus, venue_order_id="42"):
            self._bus = bus
            self._venue_order_id = venue_order_id

        def submit(self, request):
            from vike_trader_app.exec.events import OrderAccepted as _OA, OrderSubmitted as _OS
            self._bus.publish(_OS(client_order_id=request.client_order_id))
            self._bus.publish(_OA(client_order_id=request.client_order_id,
                                  venue_order_id=self._venue_order_id))

        def detach(self):
            pass

    bus = EventBus()
    client = _SyncClient(bus, venue_order_id="okx-ord-1")
    hub = LiveOmsHub(
        bus=bus,
        account=Account(),
        gate=RiskGate(RiskLimits()),
        client=client,
        venue="okx",
        symbol="BTC-USDT",
    )

    # Submit a ticket for coid-1 — client publishes OrderSubmitted + OrderAccepted synchronously
    req = OrderRequest(
        client_order_id="coid-1",
        venue="okx",
        symbol="BTC-USDT",
        side=+1,
        qty=0.001,
        order_type="limit",
        price=50000.0,
    )
    hub.submit_ticket(req)
    # Verify the order reached ACCEPTED via the sync client
    assert hub.registry["coid-1"].status is OrderStatus.ACCEPTED

    # Build the fill frame via the mapper
    row = _order_row(
        state="filled",
        fillSz="0.001",
        sz="0.001",
        tradeId="T1",
        clOrdId="coid-1",
        instId="BTC-USDT",
        side="buy",
        fillPx="50000",
        fillFee="-0.05",
        accFillSz="0.001",
    )
    frame = _frame([row])
    events = map_okx_private(frame, venue="okx", symbol="BTC-USDT")

    # Publish each event from the mapper onto the hub bus (mirrors WS -> bus delivery)
    for ev in events:
        bus.publish(ev)

    # The FSM must have advanced to FILLED via the OrderFilled wrap
    mo = hub.registry["coid-1"]
    assert mo.status is OrderStatus.FILLED, (
        f"Expected FILLED but got {mo.status} — the OrderFilled wrap is missing or wrong"
    )
    assert mo.filled_qty == pytest.approx(0.001), (
        f"Expected filled_qty=0.001 but got {mo.filled_qty} — wrap not carrying fill"
    )

    # The Account position must have increased (bare FillEvent folded Account)
    pos = hub.account.positions.get(("okx", "BTC-USDT", "BOTH"))
    assert pos is not None, "Account position not created — FillEvent not published"
    assert pos["size"] == pytest.approx(0.001)
