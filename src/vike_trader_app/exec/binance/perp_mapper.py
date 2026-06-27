"""Pure Binance USDS-M futures ORDER_TRADE_UPDATE -> vike event mapper (no socket; scripted frames).

The futures event nests order fields under 'o' ({"e":"ORDER_TRADE_UPDATE","T":..,"o":{...}}) — the
SPOT executionReport is flat. We unwrap 'o' then map the SAME field letters (s/c/x/X/i/l/L/n/t/m/S)
plus the perp-only 'ps' (positionSide). x=='TRADE' is the ONLY fill execType (partial vs full read
from X). On a fill we emit BOTH a bare FillEvent AND the wrapping OrderFilled|OrderPartiallyFilled
(the dual-publish contract); wrap.fill IS the bare fill object. mark_price stays None (the event
carries no mark — it arrives from reconcile/mark feed, like Bybit's null-safe behavior). The spot
binance/mapper.py is NOT edited (byte-identical). Mirrors okx/perp_mapper.py + bybit/perp_mapper.py.

ACCOUNT_UPDATE with m=='FUNDING_FEE': emit one FundingEvent per non-zero 'bc' balance row.
  Key off a['B'], NEVER a['P'] (cross-margin FUNDING_FEE has no a['P']).
  bc is received-positive (+received / -paid) — pass through with no sign flip.
x=='TRADE' with autoclose- clientOrderId: emit PositionLiquidated ONLY (suppresses FillEvent to
  prevent double-fold in apply_liquidation).
"""
from __future__ import annotations

from vike_trader_app.exec.events import (
    FillEvent, FundingEvent, OrderAccepted, OrderCanceled, OrderExpired,
    OrderFilled, OrderPartiallyFilled, PositionLiquidated,
)

_LIQ_COID_PREFIXES = ("autoclose-", "adl_autoclose", "settlement_autoclose-")


def map_binance_perp(frame, *, venue: str = "binance", symbol: str = "") -> list[object]:
    if not isinstance(frame, dict):
        return []
    if frame.get("e") == "ACCOUNT_UPDATE":
        a = frame.get("a") or {}
        if a.get("m") != "FUNDING_FEE":
            return []                                  # ORDER/ADJUSTMENT/... -> fill-driven, ignore
        ts = int(frame.get("T", 0) or 0)
        out: list[object] = []
        for b in a.get("B", []):
            bc = float(b.get("bc", 0) or 0)
            if bc == 0.0:
                continue
            out.append(FundingEvent(
                venue=venue, symbol=symbol, position_side="BOTH",
                funding_rate=0.0, amount=bc, mark_price=None, ts=ts))
        return out
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
        if str(o.get("c", "")).startswith(_LIQ_COID_PREFIXES):
            return [PositionLiquidated(
                venue=venue,
                symbol=str(o.get("s", symbol) or symbol),
                position_side=str(o.get("ps", "BOTH")),
                qty=float(o.get("l", 0) or 0),
                liq_price=float(o.get("L", 0) or 0),
                fee=float(o.get("n", 0) or 0),
                ts=ts,
                trade_id=str(o.get("t", "")),       # OTU trade id — same 't' the fill path reads
            )]                                          # liquidation -> PositionLiquidated ONLY
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
