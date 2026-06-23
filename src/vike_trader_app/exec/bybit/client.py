"""BybitSpotExecutionClient — signed V5 REST submit/cancel/connect (ACK-only; fills via WS later).

Reuses the shared CryptoExecutionClient flow. Bybit specifics: category=spot on every call,
Buy/Sell + Market/Limit casing, orderLinkId as the client id, marketUnit=baseCoin on a Market Buy
(qty is otherwise read as QUOTE), and a 200-body retCode!=0 error model (unwrap() raises).
"""

from __future__ import annotations

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.binance.transport import get_public_json
from vike_trader_app.exec.bybit.transport import BybitApiError, bybit_signed_request
from vike_trader_app.exec.crypto_client import CryptoExecutionClient

# order-not-exists / too-late-to-cancel (spot family) — swallowed by cancel()
_NOT_FOUND = frozenset({110001, 170213})


class BybitSpotExecutionClient(CryptoExecutionClient):
    VENUE = "bybit"
    PATH_ORDER_CREATE = "/v5/order/create"
    PATH_ORDER_CANCEL = "/v5/order/cancel"
    PATH_OPEN_ORDERS = "/v5/order/realtime"
    PATH_ACCOUNT = "/v5/account/wallet-balance"
    PATH_TICKER = "/v5/market/tickers"
    CREATE_METHOD = "POST"
    CANCEL_METHOD = "POST"

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", transport=bybit_signed_request,
                 public_transport=get_public_json) -> None:
        super().__init__(bus, signer=signer, rest_base_url=rest_base_url, symbol=symbol,
                         filters=filters, base_asset=base_asset, transport=transport,
                         public_transport=public_transport)

    def build_order_params(self, request) -> dict:
        is_limit = request.order_type.lower() == "limit"
        params = {
            "category": "spot",
            "symbol": self._symbol,
            "side": "Buy" if request.side > 0 else "Sell",
            "orderType": "Limit" if is_limit else "Market",
            "qty": format_qty(request.qty, self._filters["step_size"]),
            "orderLinkId": request.client_order_id,
        }
        if not is_limit and request.side > 0:
            params["marketUnit"] = "baseCoin"  # qty is BASE units, not quote USDT
        if is_limit:
            params["timeInForce"] = "GTC"
            params["price"] = format_price(request.price, self._filters["tick_size"])
        return params

    def build_cancel_params(self, client_order_id) -> dict:
        return {"category": "spot", "symbol": self._symbol, "orderLinkId": client_order_id}

    def build_account_params(self) -> dict:
        return {"accountType": "UNIFIED"}

    def build_open_orders_params(self) -> dict:
        return {"category": "spot", "symbol": self._symbol}

    def build_ticker_params(self) -> dict:
        return {"category": "spot", "symbol": self._symbol}

    def parse_venue_order_id(self, result) -> str:
        return str(result.get("orderId", ""))

    def iter_balances(self, result):
        for acct in result.get("list", []):
            for coin in acct.get("coin", []):
                # Use availableToWithdraw (UNIFIED field: total minus qty locked in open sell orders)
                # so the base's seeded_size = availableToWithdraw + locked_sell_qty = total held base.
                # walletBalance is the TOTAL balance and would double-count locked sell qty.
                free = coin.get("availableToWithdraw", coin.get("walletBalance", 0))
                yield {"asset": coin.get("coin"), "free": free}

    def iter_open_orders(self, result):
        for o in result.get("list", []):
            price = o.get("price")
            yield {
                "side": +1 if o.get("side") == "Buy" else -1,
                "orig_qty": float(o.get("qty", 0) or 0),
                "executed_qty": float(o.get("cumExecQty", 0) or 0),
                "coid": str(o.get("orderLinkId", "")),
                "order_type": str(o.get("orderType", "")).lower(),
                "price": float(price) if price not in (None, "", "0") else None,
                "venue_order_id": str(o.get("orderId", "")),
            }

    def parse_mark_px(self, ticker_resp) -> float:
        # ticker_resp is already unwrapped by the base: parse_mark_px(self.unwrap(raw_ticker)) -> {"list":[...]}
        lst = ticker_resp.get("list", [])
        return float(lst[0].get("lastPrice", 0) or 0) if lst else 0.0

    def is_order_not_found(self, code) -> bool:
        return code in _NOT_FOUND

    def unwrap(self, resp):
        if resp.get("retCode", 0) != 0:
            raise BybitApiError(int(resp["retCode"]), str(resp.get("retMsg", "")))
        return resp.get("result", {})
