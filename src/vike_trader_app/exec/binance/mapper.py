"""Pure Binance executionReport -> vike event mapper (no socket; unit-tested with scripted frames).

The WS executionReport is the SOLE source of truth for fills. On x=TRADE we emit BOTH a bare
FillEvent (the Account folds it) AND the wrapping OrderPartiallyFilled/OrderFilled (the FSM registry
applies it) — the dual-publish contract. Commission is carried on FillEvent.commission, NEVER netted
into last_px. trade_id = Binance `t` (the reconnect dedup key); client_order_id = `c`.
"""

from __future__ import annotations

from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
)


def map_execution_report(frame: dict, *, venue: str, symbol: str) -> list[object]:
    """Map one executionReport to frozen vike events ([] for an unknown exec type).

    The WS stream is account-wide; the frame's `s` field carries the true order symbol. We read
    it from the frame and fall back to the passed `symbol` only when `s` is absent.
    """
    symbol = str(frame.get("s", symbol))
    coid = str(frame.get("c", ""))
    ts = int(frame.get("T", 0))
    x = frame.get("x")

    if x == "NEW":
        return [OrderAccepted(client_order_id=coid, venue_order_id=str(frame.get("i", "")), ts=ts)]
    if x == "CANCELED":
        return [OrderCanceled(client_order_id=coid, reason=str(frame.get("r", "")), ts=ts)]
    if x == "REJECTED":
        return [OrderRejected(client_order_id=coid, reason=str(frame.get("r", "")), ts=ts)]
    if x == "EXPIRED":
        return [OrderExpired(client_order_id=coid, ts=ts)]
    if x == "TRADE":
        fill = FillEvent(
            trade_id=str(frame.get("t", "")),
            client_order_id=coid,
            venue=venue,
            symbol=symbol,
            side=+1 if frame.get("S") == "BUY" else -1,
            last_qty=float(frame.get("l", 0) or 0),
            last_px=float(frame.get("L", 0) or 0),
            commission=float(frame.get("n", 0) or 0),
            liquidity_side="maker" if frame.get("m") else "taker",
            ts=ts,
        )
        wrap_cls = OrderFilled if frame.get("X") == "FILLED" else OrderPartiallyFilled
        return [fill, wrap_cls(client_order_id=coid, fill=fill, ts=ts)]
    return []


def map_binance_private(frame, *, venue: str = "binance", symbol: str = "") -> list[object]:
    """Dispatch a Binance WS-API user-data frame -> vike events.

    non-dict                                                  -> []
    ACK echo (top-level 'status'/'result'/'error' present)    -> []
    inner = frame.get('event', frame)  (unwrap WS-API envelope, tolerate raw)
    inner.get('e') == 'executionReport'  -> map_execution_report(inner, venue=venue, symbol=symbol)
    anything else (outboundAccountPosition, balanceUpdate, …) -> []
    """
    if not isinstance(frame, dict):
        return []
    # Subscribe ACK / error echo: top-level status/result/error key present
    if "status" in frame or "result" in frame or "error" in frame:
        return []
    # Unwrap WS-API envelope {"subscriptionId":0, "event":{...}} — tolerate raw too
    inner = frame.get("event", frame)
    if isinstance(inner, dict) and inner.get("e") == "executionReport":
        return map_execution_report(inner, venue=venue, symbol=symbol)
    return []
