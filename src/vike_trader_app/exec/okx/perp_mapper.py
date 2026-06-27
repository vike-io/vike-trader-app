"""OKX SWAP (linear perp) private-WS decoder: reuses the spot map_okx_order, then enriches
each FillEvent with fillSz rescaled from CONTRACTS to BASE (× ct_val), mark_price from
fillMarkPx or markPx, and position_side from posSide. Spot mapper.py is NOT edited
(byte-identical constraint). Mirrors bybit/perp_mapper.py in shape exactly.
"""
from __future__ import annotations

import dataclasses

from vike_trader_app.exec.okx.mapper import map_okx_order
from vike_trader_app.exec.events import FillEvent, OrderFilled, OrderPartiallyFilled, PositionLiquidated

_POSSIDE_MAP = {"net": "BOTH", "long": "LONG", "short": "SHORT"}

# Full OKX 'category' enum: {normal, twap, adl, full_liquidation, partial_liquidation, delivery, ddh}.
# Liquidation/ADL = exactly these three; 'delivery' (expiry settlement) and 'ddh' (delta-hedge) are
# intentionally EXCLUDED — they are not forced liquidations.
_LIQ_CATEGORIES = {"full_liquidation", "partial_liquidation", "adl"}


def _okx_liquidation_event(row: dict, *, venue: str, symbol: str, ct_val: float) -> PositionLiquidated:
    px_raw = row.get("fillPx") or row.get("px") or 0
    return PositionLiquidated(
        venue=venue,
        symbol=str(row.get("instId", symbol)),
        position_side=_POSSIDE_MAP.get(str(row.get("posSide", "net")), "BOTH"),
        qty=float(row.get("fillSz", 0) or 0) * ct_val,   # contracts -> base, same rescale as fills
        liq_price=float(px_raw or 0),
        fee=abs(float(row.get("fillFee", 0) or 0)),
        ts=int(row.get("fillTime") or row.get("uTime") or 0),
        trade_id=str(row.get("tradeId", "")),   # guaranteed non-empty by the has_fill gate (line 92)
    )


def _enrich_perp_okx(events: list[object], row: dict, ct_val: float) -> list[object]:
    """Rescale FillEvent.last_qty contracts->base, carry mark_price, set position_side.

    Non-fill events (OrderAccepted, OrderRejected, OrderCanceled) pass through UNCHANGED.
    The OrderFilled/OrderPartiallyFilled wrap.fill is re-bound to the same enriched fill
    object so dual-publish identity is preserved (evs[1].fill is evs[0]).
    """
    mark_raw = row.get("fillMarkPx") or row.get("markPx")
    mark: float | None = float(mark_raw) if mark_raw not in (None, "", "0") else None
    pside = _POSSIDE_MAP.get(str(row.get("posSide", "net")), "BOTH")

    out: list[object] = []
    new_fill: FillEvent | None = None
    for ev in events:
        if isinstance(ev, FillEvent):
            new_fill = dataclasses.replace(
                ev,
                last_qty=ev.last_qty * ct_val,
                mark_price=mark,
                position_side=pside,
            )
            out.append(new_fill)
        elif isinstance(ev, (OrderFilled, OrderPartiallyFilled)) and new_fill is not None:
            out.append(dataclasses.replace(ev, fill=new_fill))  # keep wrap.fill identical to fill
        else:
            out.append(ev)
    return out


def map_okx_perp(frame: dict, *, venue: str = "okx", symbol: str = "", ct_val: float) -> list[object]:
    """Dispatch OKX SWAP private WS frame -> vike events (per-row, mirrors map_okx_private guard).

    Replicates the map_okx_private dispatch guard (non-dict -> []; event ack -> [];
    no arg -> []; channel != 'orders' -> []), then per row calls map_okx_order and
    enriches any FillEvent: rescales last_qty contracts->base (*ct_val), carries
    mark_price from fillMarkPx/markPx, sets position_side from posSide.
    Dual-publish preserved: OrderFilled|OrderPartiallyFilled wrap.fill is re-bound
    to the rescaled fill (identity). Spot mapper.py is byte-identical (untouched).
    """
    if not isinstance(frame, dict):
        return []
    if frame.get("event") is not None:
        return []
    arg = frame.get("arg")
    if not isinstance(arg, dict):
        return []
    if arg.get("channel") != "orders":
        return []

    events: list[object] = []
    for item in frame.get("data", []):
        # OKX pushes `category` on EVERY orders-channel frame — INCLUDING non-fill lifecycle frames
        # (state=='live' placement, state in {canceled,mmp_canceled}), which carry fillSz=''/tradeId=''.
        # Only a real liquidation FILL (gated on the SAME has_fill map_okx_order uses) becomes a
        # PositionLiquidated; a non-fill liq-category frame MUST fall through to its normal
        # OrderAccepted/OrderCanceled lifecycle — else _okx_liquidation_event would build qty=0 /
        # liq_price=0 and apply_liquidation (which ignores ev.qty) would flatten the WHOLE book at 0.
        fill_sz_raw = str(item.get("fillSz") or "0")
        has_fill = fill_sz_raw not in ("", "0") and bool(item.get("tradeId"))
        if has_fill and str(item.get("category", "")) in _LIQ_CATEGORIES:
            events.append(_okx_liquidation_event(item, venue=venue, symbol=symbol, ct_val=ct_val))
            continue   # liquidation FILL -> PositionLiquidated ONLY; never a FillEvent
        row_events = map_okx_order(item, venue=venue, symbol=symbol)
        events.extend(_enrich_perp_okx(row_events, item, ct_val))
    return events
