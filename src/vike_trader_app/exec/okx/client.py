"""OKXSpotExecutionClient — signed V5 REST submit/cancel (ACK-only; fills via WS later).

Reuses the shared CryptoExecutionClient flow. OKX specifics: instId (BTC-USDT dashed form),
tdMode=cash, buy/sell lowercase, clOrdId as the client id, tgtCcy=base_ccy ONLY on a Market Buy
(sz is otherwise read as QUOTE USDT by OKX), and a two-level error model:
  - top-level code != "0" → OKXApiError
  - top-level code == "0" but data[0].sCode != "0" → OKXApiError (partial-batch per-order error)
"""

from __future__ import annotations

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.okx.transport import OKXApiError, okx_public_get, okx_signed_request
from vike_trader_app.exec.crypto_client import CryptoExecutionClient

# cancel: filled / already-canceled / does-not-exist family — swallowed by cancel()
_NOT_FOUND = frozenset({51400, 51401, 51402})


class OKXSpotExecutionClient(CryptoExecutionClient):
    VENUE = "okx"
    PATH_ORDER_CREATE = "/api/v5/trade/order"
    PATH_ORDER_CANCEL = "/api/v5/trade/cancel-order"
    PATH_OPEN_ORDERS = "/api/v5/trade/orders-pending"
    PATH_ACCOUNT = "/api/v5/account/balance"
    PATH_TICKER = "/api/v5/market/ticker"
    CREATE_METHOD = "POST"
    CANCEL_METHOD = "POST"
    # OKX availBal is the FREE balance; the base connect() must add locked_sell_qty on top.
    BALANCE_IS_TOTAL = False

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", transport=okx_signed_request,
                 public_transport=okx_public_get) -> None:
        super().__init__(bus, signer=signer, rest_base_url=rest_base_url, symbol=symbol,
                         filters=filters, base_asset=base_asset, transport=transport,
                         public_transport=public_transport)

    def build_order_params(self, request) -> dict:
        is_limit = request.order_type.lower() == "limit"
        params = {
            "instId": self._symbol,
            "tdMode": "cash",
            "side": "buy" if request.side > 0 else "sell",
            "ordType": "limit" if is_limit else "market",
            "sz": format_qty(request.qty, self._filters["step_size"]),
            "clOrdId": request.client_order_id,
        }
        if not is_limit and request.side > 0:
            params["tgtCcy"] = "base_ccy"  # market-BUY sz is BASE units, not quote USDT
        if is_limit:
            params["px"] = format_price(request.price, self._filters["tick_size"])
        return params

    def build_cancel_params(self, client_order_id) -> dict:
        return {"instId": self._symbol, "clOrdId": client_order_id}

    def build_account_params(self) -> dict:
        return {}

    def build_open_orders_params(self) -> dict:
        return {"instType": "SPOT", "instId": self._symbol}

    def build_ticker_params(self) -> dict:
        return {"instId": self._symbol}

    def parse_venue_order_id(self, result) -> str:
        # result is the unwrapped LIST (data); data[0].ordId is the venue order id
        return str(result[0].get("ordId", "")) if result else ""

    def iter_balances(self, result):
        for acct in result:                       # result == data (list); usually one entry
            for d in acct.get("details", []):
                yield {"asset": d.get("ccy"), "free": d.get("availBal", 0)}   # availBal = free/spendable

    def iter_open_orders(self, result):
        for o in result:
            px = o.get("px")
            yield {
                "side": +1 if o.get("side") == "buy" else -1,
                "orig_qty": float(o.get("sz", 0) or 0),
                "executed_qty": float(o.get("accFillSz", 0) or 0),
                "coid": str(o.get("clOrdId", "")),
                "order_type": str(o.get("ordType", "")).lower(),
                "price": float(px) if px not in (None, "", "0") else None,
                "venue_order_id": str(o.get("ordId", "")),
            }

    def parse_mark_px(self, ticker_resp) -> float:
        return float(ticker_resp[0].get("last", 0) or 0) if ticker_resp else 0.0

    def is_order_not_found(self, code: int) -> bool:
        return code in _NOT_FOUND

    def unwrap(self, resp):
        if str(resp.get("code", "0")) != "0":
            data = resp.get("data") or []
            if data and str(data[0].get("sCode", "0")) != "0":
                raise OKXApiError(int(data[0]["sCode"]), str(data[0].get("sMsg", "")))
            raise OKXApiError(int(resp["code"]), str(resp.get("msg", "")))
        data = resp.get("data", [])
        # success top-level but a per-order sCode failure (place/cancel partial-batch semantics)
        if data and isinstance(data[0], dict) and "sCode" in data[0] and str(data[0]["sCode"]) != "0":
            raise OKXApiError(int(data[0]["sCode"]), str(data[0].get("sMsg", "")))
        return data
