"""Pure Binance USDS-M futures ORDER_TRADE_UPDATE -> vike event mapper (no socket; scripted frames).

The futures event nests order fields under 'o' ({"e":"ORDER_TRADE_UPDATE","T":..,"o":{...}}) — the
SPOT executionReport is flat. We unwrap 'o' then map the SAME field letters (s/c/x/X/i/l/L/n/t/m/S)
plus the perp-only 'ps' (positionSide). x=='TRADE' is the ONLY fill execType (partial vs full read
from X). On a fill we emit BOTH a bare FillEvent AND the wrapping OrderFilled|OrderPartiallyFilled
(the dual-publish contract); wrap.fill IS the bare fill object. mark_price stays None (the event
carries no mark — it arrives from reconcile/mark feed, like Bybit's null-safe behavior). The spot
binance/mapper.py is NOT edited (byte-identical). Mirrors okx/perp_mapper.py + bybit/perp_mapper.py.
"""
from __future__ import annotations

from vike_trader_app.exec.events import (
    FillEvent, OrderAccepted, OrderCanceled, OrderExpired, OrderFilled, OrderPartiallyFilled,
)


def map_binance_perp(frame, *, venue: str = "binance", symbol: str = "") -> list[object]:
    if not isinstance(frame, dict):
        return []
    if frame.get("e") != "ORDER_TRADE_UPDATE":
        return []
    o = frame.get("o")
    if not isinstance(o, dict):
        return []

    coid = str(o.get("c", ""))
    ts = int(frame.get("T", 0) or 0)
    x = o.get("x")

    if x == "NEW":
        return [OrderAccepted(client_order_id=coid, venue_order_id=str(o.get("i", "")), ts=ts)]
    if x == "CANCELED":
        return [OrderCanceled(client_order_id=coid, ts=ts)]
    if x == "EXPIRED":
        return [OrderExpired(client_order_id=coid, ts=ts)]
    if x == "TRADE":
        fill = FillEvent(
            trade_id=str(o.get("t", "")),
            client_order_id=coid,
            venue=venue,
            symbol=str(o.get("s", symbol) or symbol),
            side=+1 if o.get("S") == "BUY" else -1,
            last_qty=float(o.get("l", 0) or 0),
            last_px=float(o.get("L", 0) or 0),
            commission=float(o.get("n", 0) or 0),
            liquidity_side="maker" if o.get("m") else "taker",
            ts=ts,
            mark_price=None,                                  # ORDER_TRADE_UPDATE has no mark
            position_side=str(o.get("ps", "BOTH")),           # Binance uses BOTH/LONG/SHORT literally
        )
        wrap_cls = OrderFilled if o.get("X") == "FILLED" else OrderPartiallyFilled
        return [fill, wrap_cls(client_order_id=coid, fill=fill, ts=ts)]
    return []                                                 # CALCULATED / AMENDMENT / unknown
