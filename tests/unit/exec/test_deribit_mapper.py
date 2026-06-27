"""Pure-mapper tests for exec/deribit/mapper.py (no socket, no Qt).

All scripted frames use real field names from the Deribit user.trades WebSocket spec
(docs.deribit.com/subscriptions/user/usertradeskindcurrencyinterval.md). The INTEGRATION
test proves the OrderFilled wrap drives the ManagedOrder FSM offline so the bug cannot hide
behind a green mapper and only fail the network smoke. The CROSS-SYMBOL test proves a fill for
a different instrument is DROPPED by LiveOmsHub._on_event (line 159: event.symbol != self.symbol).
"""
from __future__ import annotations

import pytest

from vike_trader_app.exec.events import FillEvent, OrderFilled, OrderPartiallyFilled
from vike_trader_app.exec.deribit.mapper import map_deribit_private, map_deribit_trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trade(**kw) -> dict:
    """Baseline user.trades trade object (real Deribit field names), overridable via kwargs."""
    row = {
        "trade_id": "48079262",
        "trade_seq": 74405,
        "order_id": "4008978075",
        "instrument_name": "BTC-25SEP20-9000-C",
        "direction": "buy",
        "amount": 1.5,
        "price": 0.025,
        "fee": 0.00049961,
        "fee_currency": "BTC",
        "liquidity": "T",
        "state": "filled",
        "timestamp": 1590484255886,
        "mark_price": 0.0261,
        "label": "coid-1",
    }
    row.update(kw)
    return row


def _frame(rows: list[dict], channel: str = "user.trades.option.BTC.raw") -> dict:
    """Wrap rows in a minimal user.trades subscription notification frame."""
    return {
        "jsonrpc": "2.0",
        "method": "subscription",
        "params": {"channel": channel, "data": rows},
    }


# ---------------------------------------------------------------------------
# Per-row fill tests (map_deribit_trade)
# ---------------------------------------------------------------------------

def test_buy_fill_full():
    """Full taker buy fill: [FillEvent, OrderFilled] with correct fields including ABS commission."""
    result = map_deribit_trade(_trade(), venue="deribit", symbol="BTC-25SEP20-9000-C")
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderFilled)
    assert fill.trade_id == "48079262"
    assert fill.client_order_id == "coid-1"
    assert fill.venue == "deribit"
    assert fill.symbol == "BTC-25SEP20-9000-C"
    assert fill.side == +1
    assert fill.last_qty == 1.5            # COIN units — no contract rescale
    assert fill.last_px == 0.025
    assert fill.commission == pytest.approx(0.00049961)
    assert fill.liquidity_side == "taker"  # liquidity 'T' -> taker
    assert fill.position_side == "BOTH"    # options are one-way; spot-identical
    assert fill.mark_price is None         # options do not seed perp mark
    assert fill.ts == 1590484255886
    assert wrap.client_order_id == "coid-1"
    assert wrap.fill is fill               # identity — drives the FSM without double-fold


def test_sell_maker_fill():
    """Sell, maker fill -> side==-1, liquidity_side=='maker'."""
    result = map_deribit_trade(
        _trade(direction="sell", liquidity="M", trade_id="T2"),
        venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    fill, wrap = result
    assert fill.side == -1
    assert fill.liquidity_side == "maker"
    assert isinstance(wrap, OrderFilled)


def test_negative_fee_is_abs_commission():
    """Deribit fee can be NEGATIVE (maker rebate); commission must be positive (abs)."""
    result = map_deribit_trade(
        _trade(fee=-2.1e-7, trade_id="T3"), venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    fill = result[0]
    assert fill.commission == pytest.approx(2.1e-7)


def test_open_state_is_partial():
    """state=='open' (resting order, intermediate partial) -> OrderPartiallyFilled."""
    result = map_deribit_trade(
        _trade(state="open", amount=0.5, trade_id="T4"),
        venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    assert len(result) == 2
    fill, wrap = result
    assert isinstance(fill, FillEvent)
    assert isinstance(wrap, OrderPartiallyFilled)
    assert fill.last_qty == 0.5


def test_filled_state_is_orderfilled():
    """state=='filled' -> OrderFilled (terminal)."""
    result = map_deribit_trade(
        _trade(state="filled", trade_id="T5"), venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    assert isinstance(result[1], OrderFilled)


def test_missing_label_tolerated():
    """A trade object with no 'label' -> client_order_id == '' (registry lookup no-ops downstream)."""
    row = _trade(trade_id="T6")
    del row["label"]
    result = map_deribit_trade(row, venue="deribit", symbol="BTC-25SEP20-9000-C")
    fill, wrap = result
    assert fill.client_order_id == ""
    assert wrap.client_order_id == ""


def test_symbol_passthrough_not_filtered():
    """Mapper does NOT filter by symbol — uses the row's instrument_name (hub guards symbol)."""
    result = map_deribit_trade(
        _trade(instrument_name="ETH-25SEP20-300-P", trade_id="T7"),
        venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    assert result[0].symbol == "ETH-25SEP20-300-P"


# ---------------------------------------------------------------------------
# Dispatch tests (map_deribit_private)
# ---------------------------------------------------------------------------

def test_user_trades_frame_dispatches():
    result = map_deribit_private(_frame([_trade(trade_id="T8")]), venue="deribit",
                                 symbol="BTC-25SEP20-9000-C")
    assert len(result) == 2
    assert isinstance(result[0], FillEvent)
    assert isinstance(result[1], OrderFilled)


def test_multiple_trades_in_one_frame():
    """params.data is a LIST — each trade yields a [FillEvent, wrap] pair."""
    result = map_deribit_private(
        _frame([_trade(trade_id="A"), _trade(trade_id="B", state="open")]),
        venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    assert len(result) == 4
    assert [type(e).__name__ for e in result] == [
        "FillEvent", "OrderFilled", "FillEvent", "OrderPartiallyFilled"]


def test_auth_result_returns_empty():
    """The public/auth RESULT (carries 'id'+'result', no 'method') -> []."""
    assert map_deribit_private(
        {"jsonrpc": "2.0", "id": 1, "result": {"access_token": "X", "refresh_token": "Y"}}) == []


def test_subscribe_ack_returns_empty():
    """The private/subscribe ACK (carries 'id'+'result' channel echo, no 'method') -> []."""
    assert map_deribit_private(
        {"jsonrpc": "2.0", "id": 2, "result": ["user.trades.option.BTC.raw"]}) == []


def test_non_subscription_method_returns_empty():
    """A heartbeat notification (method != 'subscription') -> []."""
    assert map_deribit_private(
        {"jsonrpc": "2.0", "method": "heartbeat", "params": {"type": "test_request"}}) == []


def test_non_dict_returns_empty():
    assert map_deribit_private("pong") == []
    assert map_deribit_private(None) == []


def test_wrong_channel_returns_empty():
    """A user.changes notification (not user.trades) -> []."""
    frame = _frame([_trade()], channel="user.changes.option.BTC.raw")
    assert map_deribit_private(frame, venue="deribit") == []


def test_data_as_dict_returns_empty():
    """Defense-in-depth: a user.* frame whose params.data is a DICT (not a list) -> []."""
    frame = {"jsonrpc": "2.0", "method": "subscription",
             "params": {"channel": "user.trades.option.BTC.raw", "data": {"not": "a list"}}}
    assert map_deribit_private(frame, venue="deribit") == []


# ---------------------------------------------------------------------------
# INTEGRATION — the OrderFilled wrap drives the ManagedOrder FSM (offline, no network)
# ---------------------------------------------------------------------------

def test_filled_row_advances_managed_order_fsm():
    """The [FillEvent, OrderFilled] pair must advance ManagedOrder to FILLED + set filled_qty.

    Without the OrderFilled wrap, filled_qty stays 0.0 and status stays ACCEPTED.
    Mirrors test_okx_mapper.test_filled_row_advances_managed_order_fsm with a sync lifecycle client.
    """
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.events import OrderAccepted as _OA, OrderRequest, OrderSubmitted as _OS
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.order import OrderStatus
    from vike_trader_app.exec.risk import RiskGate, RiskLimits

    class _SyncClient:
        def __init__(self, bus, venue_order_id="deribit-ord-1"):
            self._bus = bus
            self._venue_order_id = venue_order_id

        def submit(self, request):
            self._bus.publish(_OS(client_order_id=request.client_order_id))
            self._bus.publish(_OA(client_order_id=request.client_order_id,
                                  venue_order_id=self._venue_order_id))

        def detach(self):
            pass

    bus = EventBus()
    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=_SyncClient(bus), venue="deribit", symbol="BTC-25SEP20-9000-C",
    )
    req = OrderRequest(
        client_order_id="coid-1", venue="deribit", symbol="BTC-25SEP20-9000-C",
        side=+1, qty=1.5, order_type="limit", price=0.025,
    )
    hub.submit_ticket(req)
    assert hub.registry["coid-1"].status is OrderStatus.ACCEPTED

    frame = _frame([_trade(trade_id="T1", state="filled", direction="buy", amount=1.5,
                           price=0.025, label="coid-1", instrument_name="BTC-25SEP20-9000-C")])
    for ev in map_deribit_private(frame, venue="deribit", symbol="BTC-25SEP20-9000-C"):
        bus.publish(ev)

    mo = hub.registry["coid-1"]
    assert mo.status is OrderStatus.FILLED, f"Expected FILLED but got {mo.status}"
    assert mo.filled_qty == pytest.approx(1.5)
    pos = hub.account.positions.get(("deribit", "BTC-25SEP20-9000-C", "BOTH"))
    assert pos is not None and pos["size"] == pytest.approx(1.5)


def test_cross_symbol_fill_dropped_by_hub():
    """CRITIC FIX 3: a FillEvent whose symbol != hub.symbol is DROPPED by LiveOmsHub._on_event
    (live_oms.py:159 — 'ignore fills for other symbols (not this hub's order)').

    Maps a fill for ETH-25SEP20-300-P onto a hub subscribed to BTC-25SEP20-9000-C; the Account
    must remain empty (no position) and the hub registry must be unchanged.
    """
    from vike_trader_app.exec.accounting import Account
    from vike_trader_app.exec.bus import EventBus
    from vike_trader_app.exec.live_oms import LiveOmsHub
    from vike_trader_app.exec.risk import RiskGate, RiskLimits

    class _NoopClient:
        def submit(self, request): pass
        def detach(self): pass

    bus = EventBus()
    hub = LiveOmsHub(
        bus=bus, account=Account(), gate=RiskGate(RiskLimits()),
        client=_NoopClient(), venue="deribit", symbol="BTC-25SEP20-9000-C",
    )

    # A fill for ETH instrument — mapper produces a FillEvent with symbol="ETH-25SEP20-300-P"
    cross_frame = _frame(
        [_trade(trade_id="CROSS1", instrument_name="ETH-25SEP20-300-P", label="")],
        channel="user.trades.option.ETH.raw",
    )
    for ev in map_deribit_private(cross_frame, venue="deribit", symbol="ETH-25SEP20-300-P"):
        bus.publish(ev)

    # Hub should have dropped the fill (symbol mismatch)
    eth_pos = hub.account.positions.get(("deribit", "ETH-25SEP20-300-P", "BOTH"))
    btc_pos = hub.account.positions.get(("deribit", "BTC-25SEP20-9000-C", "BOTH"))
    assert eth_pos is None, "Cross-symbol fill was NOT dropped — ETH position appeared in BTC hub"
    assert btc_pos is None, "Cross-symbol fill polluted BTC account position"
