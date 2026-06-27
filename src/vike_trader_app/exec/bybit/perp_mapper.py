"""Perp-aware Bybit private-WS decoder: reuses the spot map_execution/map_order, then enriches
each linear FillEvent with mark_price from the execution row's markPrice. position_side stays
'BOTH' (one-way; the linear execution row carries NO positionIdx — hedge LONG/SHORT is slice 5f).
Spot mapper.py is NOT edited (byte-identical constraint)."""
from __future__ import annotations

import dataclasses

from vike_trader_app.exec.bybit.mapper import map_execution, map_order
from vike_trader_app.exec.events import (
    FillEvent, OrderFilled, OrderPartiallyFilled, PositionLiquidated,
)

_LIQ_EXECTYPES = frozenset({"BustTrade", "AdlTrade"})


def _enrich_perp(events: list[object], row: dict) -> list[object]:
    mark_raw = row.get("markPrice")
    if mark_raw in (None, ""):
        return events                      # null-safe: leave mark_price=None (matches spot)
    mark = float(mark_raw)
    out: list[object] = []
    new_fill = None
    for ev in events:
        if isinstance(ev, FillEvent):
            new_fill = dataclasses.replace(ev, mark_price=mark)
            out.append(new_fill)
        elif isinstance(ev, (OrderFilled, OrderPartiallyFilled)) and new_fill is not None:
            out.append(dataclasses.replace(ev, fill=new_fill))   # keep wrap.fill identical to fill
        else:
            out.append(ev)
    return out


def _bybit_liquidation_event(item: dict, *, venue: str, symbol: str) -> PositionLiquidated:
    return PositionLiquidated(
        venue=venue,
        symbol=str(item.get("symbol", symbol)),
        position_side="BOTH",
        qty=float(item.get("execQty", 0) or 0),
        liq_price=float(item.get("execPrice", 0) or 0),
        # execFee on a non-Trade (BustTrade/AdlTrade) row is fee-positive (a cost) — verified via the
        # live Funding probe (execFee on non-Trade rows is fee-positive). apply_liquidation does
        # balance -= ev.fee, so a positive taker fee correctly DEDUCTS. NEVER abs(), NEVER negate.
        fee=float(item.get("execFee", 0) or 0),
        ts=int(item.get("execTime", 0) or 0),
        trade_id=str(item.get("execId", "")),   # per-exec dedup key (same as spot fill trade_id)
    )


def map_bybit_perp(frame: dict, *, venue: str = "bybit", symbol: str = "") -> list[object]:
    """Dispatch like map_bybit_private; for 'execution' rows, reuse map_execution then enrich the
    FillEvent with mark_price from item['markPrice'] when present. position_side stays 'BOTH'."""
    topic = frame.get("topic")
    if topic is None:
        return []
    data: list[dict] = frame.get("data", [])
    events: list[object] = []
    if topic == "execution":
        for item in data:
            et = item.get("execType")
            if et in _LIQ_EXECTYPES:
                events.append(_bybit_liquidation_event(item, venue=venue, symbol=symbol))
                continue   # liquidation -> PositionLiquidated ONLY; never a FillEvent
            # 'Trade' -> dual-publish fill (byte-equivalent); any other (Funding/Settle/...) -> [].
            events.extend(_enrich_perp(map_execution(item, venue=venue, symbol=symbol), item))
    elif topic == "order":
        for item in data:
            events.extend(map_order(item, venue=venue, symbol=symbol))
    return events
