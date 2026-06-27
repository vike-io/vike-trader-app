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
  - liquidation category rows -> PositionLiquidated ONLY (no FillEvent double-fold)
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
    PositionLiquidated,
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


# ---------------------------------------------------------------------------
# 2A — liquidation category rows must emit PositionLiquidated ONLY (5e)
# ---------------------------------------------------------------------------

def test_full_liquidation_emits_liquidation_only():
    # tradeId POPULATED: the current mapper WOULD emit [FillEvent, OrderFilled] for this row, so the
    # category branch suppressing it is load-bearing (verified live).
    evs = map_okx_perp(
        _orders_frame(category="full_liquidation", fillSz="5", fillPx="60000",
                      fillFee="-0.5", posSide="net", tradeId="T9"),
        venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert [type(e).__name__ for e in evs] == ["PositionLiquidated"]
    ev = evs[0]
    assert isinstance(ev, PositionLiquidated)
    assert ev.qty == pytest.approx(0.05)     # 5 contracts × 0.01 (same rescale as fills)
    assert ev.liq_price == 60000.0
    assert ev.fee == 0.5                      # abs of fillFee
    assert ev.position_side == "BOTH"
    assert not any(isinstance(e, FillEvent) for e in evs)


def test_partial_liquidation_emits_liquidation_only():
    evs = map_okx_perp(_orders_frame(category="partial_liquidation", fillSz="2", fillPx="61000",
                                     tradeId="T8"),
                       venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert [type(e).__name__ for e in evs] == ["PositionLiquidated"]
    assert evs[0].qty == pytest.approx(0.02)


def test_adl_emits_liquidation_only_and_posside_maps():
    evs = map_okx_perp(_orders_frame(category="adl", fillSz="3", posSide="long", tradeId="T7"),
                       venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert [type(e).__name__ for e in evs] == ["PositionLiquidated"]
    assert evs[0].position_side == "LONG"


def test_normal_category_fill_unchanged():
    # fill-regression guard: a normal (category='normal') fill row still dual-publishes.
    evs = map_okx_perp(_orders_frame(category="normal"), venue="okx",
                       symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderFilled"]
    assert evs[1].fill is evs[0]


def test_okx_partial_liquidation_carries_trade_id():
    from vike_trader_app.exec.okx.perp_mapper import map_okx_perp
    from vike_trader_app.exec.events import PositionLiquidated

    frame = {"arg": {"channel": "orders", "instType": "SWAP"}, "data": [{
        "category": "partial_liquidation", "instId": "BTC-USDT-SWAP", "posSide": "net",
        "fillSz": "1", "fillPx": "60.0", "fillFee": "-0.5", "tradeId": "T-77", "fillTime": "2"}]}
    evs = map_okx_perp(frame, venue="okx", symbol="BTC-USDT-SWAP", ct_val=1.0)
    assert len(evs) == 1
    assert isinstance(evs[0], PositionLiquidated)
    assert evs[0].trade_id == "T-77"
    assert evs[0].qty == 1.0          # fillSz * ct_val — the real partial qty, not the whole book


def test_liq_category_on_nonfill_lifecycle_frame_does_not_liquidate():
    # REGRESSION (whole-branch review, CRITICAL): OKX pushes `category` on EVERY orders frame,
    # INCLUDING the non-fill placement (state='live', fillSz='0', tradeId='') and cancel of a
    # liquidation order. Without the has_fill gate, that frame built PositionLiquidated(qty=0,
    # liq_price=0) and apply_liquidation (which ignores ev.qty) flattened the WHOLE book at price 0.
    # A non-fill liq-category frame MUST fall through to its normal lifecycle event, never liquidate.
    live = map_okx_perp(
        _orders_frame(category="full_liquidation", state="live", fillSz="0", tradeId="",
                      clOrdId="c-liq", ordId="L9"),
        venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert not any(isinstance(e, PositionLiquidated) for e in live)
    assert [type(e).__name__ for e in live] == ["OrderAccepted"]
    assert live[0].venue_order_id == "L9"
    # A canceled liquidation-order frame (no fill) likewise must not flatten the book.
    canceled = map_okx_perp(
        _orders_frame(category="full_liquidation", state="canceled", fillSz="0", tradeId="",
                      clOrdId="c-liq"),
        venue="okx", symbol="BTC-USDT-SWAP", ct_val=_ct_val)
    assert not any(isinstance(e, PositionLiquidated) for e in canceled)
