"""Perp-aware Bybit private-WS decoder: reuses the spot map_execution/map_order, then enriches
each linear FillEvent with mark_price and position_side (from positionIdx: 0/absent->BOTH,
1->LONG, 2->SHORT). Spot mapper.py is NOT edited (byte-identical constraint)."""
from __future__ import annotations

import dataclasses

from vike_trader_app.exec.bybit.mapper import map_execution, map_order
from vike_trader_app.exec.events import (
    FillEvent, OrderFilled, OrderPartiallyFilled, PositionLiquidated,
)

_LIQ_EXECTYPES = frozenset({"BustTrade", "AdlTrade"})
_POSITION_IDX_SIDE = {0: "BOTH", 1: "LONG", 2: "SHORT"}


def _pside_from_idx(row: dict) -> str:
    """Bybit V5 linear positionIdx -> vike position_side. 0/absent -> BOTH (one-way, byte-equiv)."""
    try:
        idx = int(row.get("positionIdx", 0))
    except (TypeError, ValueError):
        idx = 0
    return _POSITION_IDX_SIDE.get(idx, "BOTH")


def _enrich_perp(events: list[object], row: dict) -> list[object]:
    pside = _pside_from_idx(row)                 # 5g-3: LONG/SHORT from positionIdx (0/absent->BOTH)
    mark_raw = row.get("markPrice")
    mark = None if mark_raw in (None, "") else float(mark_raw)
    out: list[object] = []
    new_fill = None
    for ev in events:
        if isinstance(ev, FillEvent):
            new_fill = dataclasses.replace(ev, mark_price=mark, position_side=pside)
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
        position_side=_pside_from_idx(item),     # 5g-3: hedge leg from positionIdx (0/absent->BOTH)
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
    FillEvent with mark_price and position_side from positionIdx (0/absent->BOTH, 1->LONG, 2->SHORT)."""
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
