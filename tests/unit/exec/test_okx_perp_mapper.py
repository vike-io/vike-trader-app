"""Tests for the OKX SWAP (linear perp) WS fill decoder (map_okx_perp).

Scripted frames — no socket. Tests verify:
  - fillSz CONTRACTS -> BASE rescale via ct_val (the #1 SWAP trap)
  - mark_price carried from fillMarkPx (primary) or markPx (fallback)
  - position_side from posSide: net->BOTH, long->LONG, short->SHORT
  - dual-publish [FillEvent, OrderFilled|OrderPartiallyFilled] contract preserved
  - wrap.fill is the SAME enriched FillEvent object (identity assertion)
  - lifecycle-only row (state=live, fillSz=0) delegates to map_okx_order UNCHANGED
  - per-row reject delegates to map_okx_order UNCHANGED
  - non-orders channel -> []
  - event ack frames and non-dict pong -> []
"""
from __future__ import annotations

import pytest

from vike_trader_app.exec.okx.perp_mapper import map_okx_perp
from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)

_ct_val = 0.01


def _orders_frame(**over):
    row = {
        "tradeId": "T1",
        "instId": "BTC-USDT-SWAP",
        "fillSz": "5",
        "fillPx": "65000",
        "fillFee": "-0.5",
        "side": "buy",
        "state": "filled",
        "execType": "T",
        "fillTime": "1700000000000",
        "clOrdId": "c-0",
        "ordId": "9",
        "sz": "5",
        "accFillSz": "5",
        "posSide": "net",
        "fillMarkPx": "65123.4",
    }
    row.update(over)
    return {"arg": {"channel": "orders", "instType": "SWAP"}, "data": [row]}


def test_swap_fill_rescales_qty_to_base_and_carries_mark():
    evs = map_okx_perp(_orders_frame(), venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderFilled"]
    assert evs[0].last_qty == pytest.approx(0.05)   # 5 contracts × 0.01 — the ctVal rescale proof
    assert evs[0].last_px == 65000.0
    assert evs[0].trade_id == "T1"
    assert evs[0].side == +1
    assert evs[0].mark_price == 65123.4
    assert evs[0].position_side == "BOTH"            # net -> BOTH
    assert evs[1].fill is evs[0]                     # dual-publish identity


def test_partial_fill_wrap_when_not_terminal():
    evs = map_okx_perp(
        _orders_frame(state="partially_filled", accFillSz="2", sz="5", fillSz="2"),
        venue="okx",
        symbol="BTC-USDT-SWAP",
        ct_val=_ct_val,
    )
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderPartiallyFilled"]
    assert isinstance(evs[1], OrderPartiallyFilled)
    assert evs[0].last_qty == pytest.approx(0.02)    # fillSz=2 contracts × 0.01
    assert evs[1].fill is evs[0]


def test_mark_from_markpx_fallback():
    frame = _orders_frame()
    del frame["data"][0]["fillMarkPx"]
    frame["data"][0]["markPx"] = "64000"
    evs = map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert evs[0].mark_price == 64000.0


def test_mark_none_when_absent():
    frame = _orders_frame()
    del frame["data"][0]["fillMarkPx"]
    evs = map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert evs[0].mark_price is None
    # dual-publish still present on the None-mark path
    assert len(evs) == 2
    assert isinstance(evs[0], FillEvent)
    assert isinstance(evs[1], OrderFilled)
    assert evs[1].fill is evs[0]


def test_posside_long_and_short_map_through():
    evs_long = map_okx_perp(_orders_frame(posSide="long"), venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert evs_long[0].position_side == "LONG"

    evs_short = map_okx_perp(_orders_frame(posSide="short"), venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert evs_short[0].position_side == "SHORT"


def test_lifecycle_only_row_delegates_unchanged():
    """state=live, no fill (fillSz=0, tradeId='') -> [OrderAccepted], no rescale path touched."""
    evs = map_okx_perp(
        _orders_frame(state="live", fillSz="0", tradeId=""),
        venue="okx",
        symbol="BTC-USDT-SWAP",
        ct_val=_ct_val,
    )
    assert [type(e).__name__ for e in evs] == ["OrderAccepted"]
    assert isinstance(evs[0], OrderAccepted)


def test_per_row_reject_delegates():
    """A row with code='51000' -> [OrderRejected], unchanged."""
    evs = map_okx_perp(
        _orders_frame(code="51000", msg="Insufficient margin", fillSz="0", tradeId=""),
        venue="okx",
        symbol="BTC-USDT-SWAP",
        ct_val=_ct_val,
    )
    assert [type(e).__name__ for e in evs] == ["OrderRejected"]
    assert isinstance(evs[0], OrderRejected)
    assert evs[0].reason == "Insufficient margin"


def test_non_orders_channel_empty():
    frame = {"arg": {"channel": "account"}, "data": []}
    assert map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val) == []


def test_event_ack_and_pong_empty():
    assert map_okx_perp({"event": "subscribe"}, venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val) == []
    assert map_okx_perp("pong", venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val) == []
