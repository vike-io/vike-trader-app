"""BinanceSpotExecutionClient — signed REST submit/cancel/connect. The WS executionReport is the
SOLE source of fills; the REST POST/DELETE response is ACK-ONLY (its fills[] is ignored). submit()
publishes OrderSubmitted then OrderAccepted/OrderRejected; cancel() issues DELETE (the OrderCanceled
arrives from the WS). qty/price are formatted to the symbol's step/tick as decimal strings to dodge
-1111 BAD_PRECISION.
"""

from __future__ import annotations

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.binance.transport import BinanceApiError, signed_request
from vike_trader_app.exec.events import (
    OrderAccepted,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
)


class BinanceSpotExecutionClient:
    """REST half of the live spot client (ACK-only); fills come from the user-data WS."""

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 transport=signed_request) -> None:
        self.bus = bus
        self._signer = signer
        self._base = rest_base_url
        self._symbol = symbol
        self._filters = filters
        self._transport = transport

    def submit(self, request: OrderRequest) -> None:
        """Publish OrderSubmitted, POST the order (ACK), publish OrderAccepted/OrderRejected."""
        self.bus.publish(OrderSubmitted(client_order_id=request.client_order_id, ts=request.ts))
        params = {
            "symbol": self._symbol,
            "side": "BUY" if request.side > 0 else "SELL",
            "type": request.order_type.upper(),
            "quantity": format_qty(request.qty, self._filters["step_size"]),
            "newClientOrderId": request.client_order_id,
            "newOrderRespType": "ACK",
        }
        if request.order_type.lower() == "limit":
            params["timeInForce"] = "GTC"
            params["price"] = format_price(request.price, self._filters["tick_size"])
        try:
            resp = self._transport(self._base, "/api/v3/order", "POST", params, self._signer)
        except BinanceApiError as exc:
            self.bus.publish(OrderRejected(client_order_id=request.client_order_id,
                                           reason=exc.msg, ts=request.ts))
            return
        self.bus.publish(OrderAccepted(client_order_id=request.client_order_id,
                                       venue_order_id=str(resp.get("orderId", "")), ts=request.ts))

    def cancel(self, client_order_id: str) -> None:
        """DELETE the resting order; the OrderCanceled state change arrives via the WS."""
        self._transport(self._base, "/api/v3/order", "DELETE",
                        {"symbol": self._symbol, "origClientOrderId": client_order_id}, self._signer)
