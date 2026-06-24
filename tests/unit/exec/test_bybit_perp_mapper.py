"""Tests for the Bybit linear-perp WS fill decoder (map_bybit_perp).

Scripted frames — no socket.  Tests verify:
  - mark_price enrichment from markPrice on linear execution rows
  - position_side == "BOTH" (one-way mode; no positionIdx on the row)
  - dual-publish [FillEvent, OrderFilled|OrderPartiallyFilled] contract preserved
  - wrap.fill is the SAME enriched FillEvent object (identity assertion)
  - None-mark path: early-return preserves dual-publish intact
  - non-Trade execType dropped
  - order topic delegates to map_order unchanged
  - op-only ack frames -> []
"""
from __future__ import annotations

from vike_trader_app.exec.bybit.perp_mapper import map_bybit_perp
from vike_trader_app.exec.events import FillEvent, OrderFilled, OrderPartiallyFilled


def _exec_frame(**over):
    row = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "execId": "e1",
        "execPrice": "65000",
        "execQty": "0.01",
        "execFee": "0.001",
        "execType": "Trade",
        "leavesQty": "0",
        "orderQty": "0.01",
        "orderLinkId": "p-0",
        "markPrice": "65123.4",
        "execTime": "1700000000000",
        "isMaker": False,
    }
    row.update(over)
    return {"topic": "execution", "data": [row]}


def test_linear_fill_carries_mark_price_and_both_side():
    evs = map_bybit_perp(_exec_frame(), venue="bybit", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["FillEvent", "OrderFilled"]
    fill = evs[0]
    assert isinstance(fill, FillEvent)
    assert fill.trade_id == "e1"
    assert fill.side == +1
    assert fill.last_qty == 0.01
    assert fill.last_px == 65000.0
    assert fill.mark_price == 65123.4          # carried from markPrice
    assert fill.position_side == "BOTH"        # one-way; NO positionIdx on the row
    # dual-publish wrap carries the SAME (enriched) fill object
    assert evs[1].fill is evs[0]


def test_partial_fill_when_leaves_nonzero():
    evs = map_bybit_perp(_exec_frame(leavesQty="0.005"), venue="bybit", symbol="BTCUSDT")
    assert isinstance(evs[1], OrderPartiallyFilled)


def test_mark_price_none_when_absent():
    f = _exec_frame()
    del f["data"][0]["markPrice"]
    evs = map_bybit_perp(f, venue="bybit", symbol="BTCUSDT")
    # null-safe: mark_price is None when markPrice absent (matches spot FillEvent default)
    assert evs[0].mark_price is None
    # dual-publish is STILL present on the None-mark path (early-return preserves contract)
    assert len(evs) == 2
    assert isinstance(evs[0], FillEvent)
    assert isinstance(evs[1], OrderFilled)
    assert evs[1].fill is evs[0]


def test_non_trade_exectype_dropped():
    assert map_bybit_perp(_exec_frame(execType="Funding"), venue="bybit", symbol="BTCUSDT") == []


def test_order_topic_delegates_unchanged():
    frame = {
        "topic": "order",
        "data": [
            {
                "orderStatus": "New",
                "orderLinkId": "p-0",
                "orderId": "9",
                "updatedTime": "1",
            }
        ],
    }
    evs = map_bybit_perp(frame, venue="bybit", symbol="BTCUSDT")
    assert [type(e).__name__ for e in evs] == ["OrderAccepted"]


def test_op_only_frame_empty():
    assert map_bybit_perp({"op": "pong"}, venue="bybit", symbol="BTCUSDT") == []
