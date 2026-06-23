"""BinanceSpotExecutionClient — signed REST submit/cancel/connect. The WS executionReport is the
SOLE source of fills; the REST POST/DELETE response is ACK-ONLY (its fills[] is ignored). submit()
publishes OrderSubmitted then OrderAccepted/OrderRejected; cancel() issues DELETE (the OrderCanceled
arrives from the WS). qty/price are formatted to the symbol's step/tick as decimal strings to dodge
-1111 BAD_PRECISION.
"""

from __future__ import annotations

from dataclasses import dataclass

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.binance.transport import BinanceApiError, get_public_json, signed_request
from vike_trader_app.exec.events import (
    OrderAccepted,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
)
from vike_trader_app.exec.order import ManagedOrder, OrderStatus


@dataclass(frozen=True)
class ReconcileSnapshot:
    positions: tuple[tuple[str, float], ...] = ()
    open_orders: tuple[ManagedOrder, ...] = ()
    # Per-position mark price at reconcile time — seeded as avg_px so an immediate close is ~0 PnL
    # instead of garbage (true cost basis is unknown for a pre-existing holding).
    position_avg_px: tuple[tuple[str, float], ...] = ()


class BinanceSpotExecutionClient:
    """REST half of the live spot client (ACK-only); fills come from the user-data WS."""

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", transport=signed_request,
                 public_transport=get_public_json) -> None:
        self.bus = bus
        self._signer = signer
        self._base = rest_base_url
        self._symbol = symbol
        self._filters = filters
        self._base_asset = base_asset
        self._transport = transport
        self._public_transport = public_transport

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
        """DELETE the resting order; the OrderCanceled state change arrives via the WS.

        -2011 ("Unknown order") means the order is already gone (filled/canceled by the WS);
        swallow it silently — the terminal state has already been/will be delivered via the WS.
        Any other error code is re-raised.
        """
        try:
            self._transport(self._base, "/api/v3/order", "DELETE",
                            {"symbol": self._symbol, "origClientOrderId": client_order_id},
                            self._signer)
        except BinanceApiError as exc:
            if exc.code == -2011:
                return  # order already gone — WS delivered/will deliver the terminal state
            raise

    def connect(self) -> ReconcileSnapshot:
        """Reconcile on connect (MAIN thread): base-asset free balance -> position; open orders ->
        ACCEPTED ManagedOrders so a later fill/cancel on a prior-session order is a legal FSM edge.

        Position size = free_base + locked_sell_qty (base locked in resting SELL orders) so that
        a fill on a reconciled SELL doesn't phantom-drive the position negative.
        avg_px is seeded from the current mark price (GET /api/v3/ticker/price) so an immediate
        close yields ~0 PnL instead of garbage (true cost basis is unknown for pre-existing lots).
        """
        account = self._transport(self._base, "/api/v3/account", "GET", {}, self._signer)
        free = 0.0
        for bal in account.get("balances", []):
            if bal.get("asset") == self._base_asset:
                free = float(bal.get("free", 0) or 0)
                break

        raw = self._transport(self._base, "/api/v3/openOrders", "GET",
                              {"symbol": self._symbol}, self._signer)

        # Add back base qty locked in resting SELL orders so position is never phantom-negative.
        locked_sell_qty = 0.0
        orders: list[ManagedOrder] = []
        for o in raw:
            if o.get("side") == "SELL":
                orig = float(o.get("origQty", 0) or 0)
                executed = float(o.get("executedQty", 0) or 0)
                locked_sell_qty += max(0.0, orig - executed)
            req = OrderRequest(
                client_order_id=str(o.get("clientOrderId", "")), venue="binance",
                symbol=self._symbol, side=+1 if o.get("side") == "BUY" else -1,
                qty=float(o.get("origQty", 0) or 0), order_type=str(o.get("type", "")).lower(),
                price=float(o["price"]) if o.get("price") not in (None, "", "0", "0.00000000") else None,
            )
            mo = ManagedOrder(request=req, status=OrderStatus.ACCEPTED,
                              venue_order_id=str(o.get("orderId", "")))
            orders.append(mo)

        seeded_size = free + locked_sell_qty

        # Fetch current mark price (unsigned public endpoint) — unknown true cost, so seed avg_px at
        # mark so an immediate close yields ~0 PnL instead of garbage.
        ticker = self._public_transport(self._base, "/api/v3/ticker/price",
                                        {"symbol": self._symbol})
        mark_px = float(ticker.get("price", 0) or 0)

        positions = ((self._symbol, seeded_size),)
        position_avg_px = ((self._symbol, mark_px),)
        return ReconcileSnapshot(positions=positions, open_orders=tuple(orders),
                                 position_avg_px=position_avg_px)
