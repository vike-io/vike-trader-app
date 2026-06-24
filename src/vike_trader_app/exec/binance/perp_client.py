"""BinancePerpExecutionClient — signed fapi (USDS-M futures) submit/cancel/reconcile + set-leverage.

Subclasses BinanceSpotExecutionClient (like the OKX/Bybit perps subclass their spot sibling):
reuses BinanceHmacSigner + signed_request + format_qty/format_price + is_order_not_found(-2011)
+ parse_venue_order_id + unwrap VERBATIM. fapi deltas: /fapi/ paths, positionSide='BOTH' (one-way;
hedge LONG/SHORT is 5f), reduceOnly as the STRING 'true'/'false', qty in BASE asset (NO ctVal),
set-leverage (idempotent HTTP-200 — NO benign swallow), and a /fapi/v2/positionRisk signed-position
reconcile (positionAmt is ALREADY SIGNED in base). PRODUCT='perp' routes connect() here. The fill
stream is the listenKey user-data WS (see binance/perp_user_data.py). binance/client.py stays
byte-identical.
"""
from __future__ import annotations

from vike_trader_app.exec.binance.client import BinanceSpotExecutionClient
from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.binance.transport import get_public_json, signed_request
from vike_trader_app.exec.crypto_client import ReconcileSnapshot


class BinancePerpExecutionClient(BinanceSpotExecutionClient):
    PRODUCT = "perp"
    PATH_ORDER_CREATE = "/fapi/v1/order"
    PATH_ORDER_CANCEL = "/fapi/v1/order"
    PATH_POSITIONS = "/fapi/v2/positionRisk"
    PATH_SET_LEVERAGE = "/fapi/v1/leverage"
    PATH_POSITION_MODE = "/fapi/v1/positionSide/dual"

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", leverage: float = 1.0,
                 transport=signed_request, public_transport=get_public_json) -> None:
        super().__init__(bus, signer=signer, rest_base_url=rest_base_url, symbol=symbol,
                         filters=filters, base_asset=base_asset, transport=transport,
                         public_transport=public_transport)
        self._leverage = leverage

    def set_leverage(self) -> None:
        """POST /fapi/v1/leverage {symbol, leverage:int}. Binance change-leverage is idempotent
        (HTTP-200 with the applied leverage even when already at target) — NO benign-error swallow."""
        params = {"symbol": self._symbol, "leverage": str(int(self._leverage))}
        self.unwrap(self._transport(self._base, self.PATH_SET_LEVERAGE, "POST",
                                    params, self._signer))

    def build_order_params(self, request) -> dict:
        is_limit = request.order_type.lower() == "limit"
        params = {
            "symbol": self._symbol,
            "side": "BUY" if request.side > 0 else "SELL",
            "type": "LIMIT" if is_limit else "MARKET",
            "quantity": format_qty(request.qty, self._filters["step_size"]),
            "newClientOrderId": request.client_order_id,
            "newOrderRespType": "ACK",
            "positionSide": "BOTH",                              # one-way; hedge LONG/SHORT is 5f
            "reduceOnly": "true" if request.reduce_only else "false",  # STRING, not bool
        }
        if is_limit:
            params["timeInForce"] = "GTC"
            params["price"] = format_price(request.price, self._filters["tick_size"])
        return params

    def reconcile_positions(self) -> ReconcileSnapshot:
        """GET /fapi/v2/positionRisk {symbol}. positionAmt is ALREADY SIGNED base qty (long>0,
        short<0 — no side lookup, no ctVal). entryPrice -> avg, markPrice -> mark. Skip non-BOTH
        (hedge) rows. One-way: one BOTH row per symbol."""
        rows = self.unwrap(self._transport(self._base, self.PATH_POSITIONS, "GET",
                                            {"symbol": self._symbol}, self._signer))
        signed = 0.0
        avg_px = 0.0
        mark_px = 0.0
        for p in rows:
            if str(p.get("positionSide", "BOTH")) != "BOTH":     # net/one-way only; skip hedge rows
                continue
            amt = float(p.get("positionAmt", 0) or 0)            # already signed
            if amt == 0.0:
                continue
            signed = amt
            avg_px = float(p.get("entryPrice", 0) or 0)
            mark_px = float(p.get("markPrice", 0) or 0)
            break                                                # one-way: at most one live row
        return ReconcileSnapshot(
            positions=((self._symbol, signed),),
            open_orders=(),
            position_avg_px=((self._symbol, avg_px),),
            position_mark_px=((self._symbol, mark_px),))
