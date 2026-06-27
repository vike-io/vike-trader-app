"""BybitPerpExecutionClient — signed V5 LINEAR perp submit/cancel/reconcile + set-leverage.

Sibling of BybitSpotExecutionClient: reuses the signer/transport/format/unwrap. Linear deltas:
category=linear, positionIdx=0 (one-way; hedge 1/2 is slice 5f), reduceOnly, NO marketUnit,
qtyStep filters, set-leverage (swallow 110043), and a /v5/position/list signed-position reconcile
(PRODUCT='perp' routes connect() here). Fills arrive via the SHARED execution WS (see perp_mapper).
"""
from __future__ import annotations

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.binance.transport import get_public_json
from vike_trader_app.exec.bybit.client import BybitSpotExecutionClient
from vike_trader_app.exec.bybit.transport import BybitApiError, bybit_signed_request
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_IDX_SIDE = {1: "LONG", 2: "SHORT"}


class BybitPerpExecutionClient(BybitSpotExecutionClient):
    PRODUCT = "perp"
    PATH_POSITION_LIST = "/v5/position/list"
    PATH_SET_LEVERAGE = "/v5/position/set-leverage"
    _LEVERAGE_NOT_MODIFIED = 110043  # "leverage not modified" — already at target, benign

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", leverage: float = 1.0,
                 transport=bybit_signed_request, public_transport=get_public_json) -> None:
        super().__init__(bus, signer=signer, rest_base_url=rest_base_url, symbol=symbol,
                         filters=filters, base_asset=base_asset, transport=transport,
                         public_transport=public_transport)
        self._leverage = leverage

    def set_leverage(self) -> None:
        """POST set-leverage (one-way: buy==sell). Swallow 110043 (already at target leverage)."""
        lev = str(self._leverage)
        params = {"category": "linear", "symbol": self._symbol,
                  "buyLeverage": lev, "sellLeverage": lev}
        try:
            self.unwrap(self._transport(self._base, self.PATH_SET_LEVERAGE, "POST",
                                        params, self._signer))
        except BybitApiError as exc:
            if exc.code == self._LEVERAGE_NOT_MODIFIED:
                return
            raise

    def build_order_params(self, request) -> dict:
        is_limit = request.order_type.lower() == "limit"
        params = {
            "category": "linear",
            "symbol": self._symbol,
            "side": "Buy" if request.side > 0 else "Sell",
            "orderType": "Limit" if is_limit else "Market",
            "qty": format_qty(request.qty, self._filters["step_size"]),
            "orderLinkId": request.client_order_id,
            "positionIdx": 0,                       # one-way; hedge 1/2 deferred to 5f
            "reduceOnly": bool(request.reduce_only),
        }
        if is_limit:
            params["timeInForce"] = "GTC"
            params["price"] = format_price(request.price, self._filters["tick_size"])
        return params

    def build_cancel_params(self, client_order_id) -> dict:
        return {"category": "linear", "symbol": self._symbol, "orderLinkId": client_order_id}

    def reconcile_positions(self) -> ReconcileSnapshot:
        """GET /v5/position/list. One-way: positionIdx==0 (side Buy/Sell signs size) -> one BOTH leg
        (byte-equivalent). Hedge: positionIdx 1 (Long) and 2 (Short) -> a LONG leg AND a SHORT leg,
        each signed (+abs for idx1/Buy, -abs for idx2/Sell) and carrying its position_side.
        """
        raw = self.unwrap(self._transport(
            self._base, self.PATH_POSITION_LIST, "GET",
            {"category": "linear", "symbol": self._symbol}, self._signer))
        legs: list[tuple[str, float, float, float, str]] = []   # (sym, signed, avg, mark, side)
        for p in raw.get("list", []):
            size = abs(float(p.get("size", 0) or 0))
            if size == 0.0:
                continue
            try:
                idx = int(p.get("positionIdx", 0))
            except (TypeError, ValueError):
                idx = 0
            side_lbl = _IDX_SIDE.get(idx, "BOTH")               # 0/other -> one-way BOTH leg
            sign = +1.0 if p.get("side") == "Buy" else -1.0
            legs.append((self._symbol, sign * size,
                         float(p.get("avgPrice", 0) or 0),
                         float(p.get("markPrice", 0) or 0),
                         side_lbl))
        if not legs:
            return ReconcileSnapshot(
                positions=((self._symbol, 0.0),), open_orders=(),
                position_avg_px=((self._symbol, 0.0),),
                position_mark_px=((self._symbol, 0.0),))
        hedge = any(sd != "BOTH" for *_r, sd in legs)
        return ReconcileSnapshot(
            positions=tuple((s, q) for s, q, _a, _m, _sd in legs),
            open_orders=(),
            position_avg_px=tuple((s, a) for s, _q, a, _m, _sd in legs),
            position_mark_px=tuple((s, m) for s, _q, _a, m, _sd in legs),
            position_sides=tuple((s, sd) for s, _q, _a, _m, sd in legs) if hedge else ())
