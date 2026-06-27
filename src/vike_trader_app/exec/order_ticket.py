"""Qt-free order-ticket seam: build a valid OrderRequest from ticket inputs + the ARMED hub's
context, and map order-lifecycle events to a one-line status string. No Qt, no hub — unit-testable.

The single correctness pin: ``hub_symbol``/``hub_venue`` come from the LIVE hub (== the venue/client
symbol), NEVER the chart symbol — they diverge for OKX ('BTC-USDT' / 'BTC-USDT-SWAP' vs 'BTCUSDT').
The gate (RiskGate.check) rounds qty/price, so the builder does NOT pre-round; it only validates the
inputs the ticket can get wrong (side, qty>0, a limit price present, a known order type).
"""

from __future__ import annotations

from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
    OrderTriggered,
)

_ORDER_TYPES = ("market", "limit")   # MVP: stop/OCO are deferred follow-ups


def build_order_request(
    *,
    hub_venue: str,
    hub_symbol: str,
    side: int,
    qty: float,
    order_type: str,
    price: float | None = None,
    reduce_only: bool = False,
    client_order_id: str,
    now_ms: int,
) -> OrderRequest:
    if side not in (1, -1):
        raise ValueError(f"side must be +1 or -1, got {side!r}")
    if qty <= 0.0:
        raise ValueError(f"qty must be > 0, got {qty!r}")
    if order_type not in _ORDER_TYPES:
        raise ValueError(f"order_type must be one of {_ORDER_TYPES}, got {order_type!r}")
    if order_type == "market":
        price = None                          # a market order values at the mark inside the gate
    elif price is None:
        raise ValueError("a limit order requires a price")
    return OrderRequest(
        client_order_id=client_order_id,
        venue=hub_venue,
        symbol=hub_symbol,                    # the VENUE/client symbol — never the chart symbol
        side=side,
        qty=float(qty),
        order_type=order_type,
        price=price,
        reduce_only=bool(reduce_only),
        ts=int(now_ms),
    )


class OrderTicketStatus:
    """Maps order-lifecycle events for the last-submitted order to a one-line status string.

    Pure + Qt-free. ``on_event`` returns None for events of OTHER orders (the bus is account-wide) so
    the caller can skip a no-op setText. OrderDenied (RiskGate pre-venue veto) and OrderRejected (venue
    reject) are DISTINCT, with distinct reason text, so the user can tell a local risk block from a
    venue rejection.
    """

    def __init__(self) -> None:
        self._coid: str | None = None

    def arm(self, client_order_id: str) -> None:
        self._coid = client_order_id

    def on_event(self, event) -> str | None:
        coid = getattr(event, "client_order_id", None)
        if self._coid is None or coid != self._coid:
            return None
        if isinstance(event, OrderSubmitted):
            return "sent"
        if isinstance(event, OrderAccepted):
            return "accepted"
        if isinstance(event, OrderTriggered):
            return "triggered"
        if isinstance(event, OrderDenied):
            return f"DENIED: {event.reason}"
        if isinstance(event, OrderRejected):
            return f"REJECTED: {event.reason}"
        if isinstance(event, OrderPartiallyFilled):
            return "partial"
        if isinstance(event, OrderFilled):
            return "filled"
        if isinstance(event, OrderCanceled):
            return "canceled"
        if isinstance(event, OrderExpired):
            return "expired"
        if isinstance(event, FillEvent):
            return f"fill {event.last_qty}@{event.last_px}"
        return None
