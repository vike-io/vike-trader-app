"""BinanceSpotExecutionClient — signed REST submit/cancel/connect. The WS executionReport is the
SOLE source of fills; the REST POST/DELETE response is ACK-ONLY (its fills[] is ignored). submit()
publishes OrderSubmitted then OrderAccepted/OrderRejected; cancel() issues DELETE (the OrderCanceled
arrives from the WS). qty/price are formatted to the symbol's step/tick as decimal strings to dodge
-1111 BAD_PRECISION.
"""

from __future__ import annotations

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.binance.transport import (
    BinanceApiError,
    get_public_json,
    signed_request,
)
from vike_trader_app.exec.crypto_client import (  # re-export
    CryptoExecutionClient,
    ReconcileSnapshot,
    VenueApiError,
)

__all__ = ["BinanceSpotExecutionClient", "ReconcileSnapshot", "BinanceApiError", "VenueApiError"]


class BinanceSpotExecutionClient(CryptoExecutionClient):
    """REST half of the live spot client (ACK-only); fills come from the user-data WS."""

    VENUE = "binance"
    PATH_ORDER_CREATE = "/api/v3/order"
    PATH_ORDER_CANCEL = "/api/v3/order"
    PATH_OPEN_ORDERS = "/api/v3/openOrders"
    PATH_ACCOUNT = "/api/v3/account"
    PATH_TICKER = "/api/v3/ticker/price"
    CREATE_METHOD = "POST"
    CANCEL_METHOD = "DELETE"

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", transport=signed_request,
                 public_transport=get_public_json) -> None:
        super().__init__(bus, signer=signer, rest_base_url=rest_base_url, symbol=symbol,
                         filters=filters, base_asset=base_asset, transport=transport,
                         public_transport=public_transport)

    def build_order_params(self, request) -> dict:
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
        return params

    def build_cancel_params(self, client_order_id) -> dict:
        return {"symbol": self._symbol, "origClientOrderId": client_order_id}

    def build_account_params(self) -> dict:
        return {}

    def build_open_orders_params(self) -> dict:
        return {"symbol": self._symbol}

    def build_ticker_params(self) -> dict:
        return {"symbol": self._symbol}

    def parse_venue_order_id(self, resp) -> str:
        return str(resp.get("orderId", ""))

    def iter_balances(self, account_resp):
        for b in account_resp.get("balances", []):
            yield {"asset": b.get("asset"), "free": b.get("free", 0)}

    def iter_open_orders(self, resp):
        for o in resp:
            price = o.get("price")
            yield {
                "side": +1 if o.get("side") == "BUY" else -1,
                "orig_qty": float(o.get("origQty", 0) or 0),
                "executed_qty": float(o.get("executedQty", 0) or 0),
                "coid": str(o.get("clientOrderId", "")),
                "order_type": str(o.get("type", "")).lower(),
                "price": float(price) if price not in (None, "", "0", "0.00000000") else None,
                "venue_order_id": str(o.get("orderId", "")),
            }

    def parse_mark_px(self, ticker_resp) -> float:
        return float(ticker_resp.get("price", 0) or 0)

    def is_order_not_found(self, code) -> bool:
        return code == -2011

    def unwrap(self, resp):
        return resp  # the Binance transport already raised BinanceApiError on a 4xx body
