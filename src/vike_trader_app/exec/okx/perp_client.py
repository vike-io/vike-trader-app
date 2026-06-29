"""OKXPerpExecutionClient — signed V5 SWAP perp submit/cancel/reconcile + set-leverage.

Subclasses OKXSpotExecutionClient to reuse signer/transport/unwrap/_NOT_FOUND/parse_venue_order_id.
SWAP deltas: instId is BTC-USDT-SWAP form, tdMode=cross (always), sz in CONTRACTS (not base),
posSide=net (one-way; hedge is 5f), NO tgtCcy, reduceOnly flag, set-leverage (empty swallow set
— OKX set-leverage is idempotent: code '0' on repeat, so re-raise on ANY non-'0'), and a signed
/api/v5/account/positions reconcile where OKX pos is already SIGNED contracts (long>0, short<0)
converted to base via ct_val. PRODUCT='perp' routes connect() here.
"""
from __future__ import annotations

import decimal

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.okx.client import OKXSpotExecutionClient
from vike_trader_app.exec.okx.transport import OKXApiError, okx_public_get, okx_signed_request
from vike_trader_app.exec.crypto_client import ReconcileSnapshot

_POSSIDE_LABEL = {"long": "LONG", "short": "SHORT"}


class OKXPerpExecutionClient(OKXSpotExecutionClient):
    PRODUCT = "perp"
    PATH_POSITIONS = "/api/v5/account/positions"
    PATH_SET_LEVERAGE = "/api/v5/account/set-leverage"
    # EMPTY — OKX set-leverage is idempotent (returns code "0" on a repeat; no benign error code
    # confirmed on demo). Re-raise on ANY non-"0" code. A specific code may be added here ONLY
    # after the demo confirms a real benign code (unlike Bybit's real 110043).
    _LEVERAGE_ALREADY_SET: frozenset[int] = frozenset()

    # unused on the 5c perp connect path (connect() -> reconcile_positions)
    def build_open_orders_params(self) -> dict:  # type: ignore[override]
        return {"instType": "SWAP", "instId": self._symbol}

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", ct_val: float, leverage: float = 1.0,
                 transport=okx_signed_request, public_transport=okx_public_get) -> None:
        super().__init__(bus, signer=signer, rest_base_url=rest_base_url, symbol=symbol,
                         filters=filters, base_asset=base_asset, transport=transport,
                         public_transport=public_transport)
        self._ct_val = ct_val
        self._leverage = leverage

    # --- SWAP always uses cross margin mode ---

    def _resolve_td_mode(self) -> str:
        """SWAP tdMode is ALWAYS cross — never the spot 'cash' auto-detect."""
        return "cross"

    # --- base ↔ contracts conversion helpers ---

    def _to_contracts(self, base_qty: float) -> float:
        """Convert base qty → contracts: base / ct_val, FLOORED to the lotSz step.

        Do NOT round to a whole contract — OKX SWAP allows FRACTIONAL contracts (lotSz e.g. 0.01),
        so the minimum order is lotSz contracts (~$10), not 1 contract (~$1000). Rounding base/ct_val
        to an int first would floor any sub-0.5-contract order to 0.
        """
        raw = decimal.Decimal(str(base_qty)) / decimal.Decimal(str(self._ct_val))
        # Floor to the lotSz (step_size) via Decimal ROUND_DOWN so partial lots are dropped
        step = decimal.Decimal(str(self._filters["step_size"]))
        contracts = (raw // step) * step
        return float(contracts)

    def _to_base(self, contracts: float) -> float:
        """Convert signed contracts → base: contracts × ct_val."""
        return contracts * self._ct_val

    # --- set-leverage ---

    def set_leverage(self) -> None:
        """POST /api/v5/account/set-leverage (mgnMode=cross). Re-raise on ANY non-'0' code.

        OKX set-leverage is idempotent — repeated calls return code '0'. _LEVERAGE_ALREADY_SET
        is intentionally EMPTY; a specific benign code may be added ONLY after demo confirmation.
        """
        lev = str(int(self._leverage)) if self._leverage == int(self._leverage) else str(self._leverage)
        params = {"instId": self._symbol, "lever": lev, "mgnMode": "cross"}
        try:
            self.unwrap(self._transport(self._base, self.PATH_SET_LEVERAGE, "POST",
                                        params, self._signer))
        except OKXApiError as exc:
            if exc.code in self._LEVERAGE_ALREADY_SET:
                return
            raise

    # --- SWAP order params ---

    def build_order_params(self, request) -> dict:
        """SWAP order: sz in CONTRACTS, tdMode=cross, posSide=net, reduceOnly, NO tgtCcy."""
        is_limit = request.order_type.lower() == "limit"
        params = {
            "instId": self._symbol,
            "tdMode": self._resolve_td_mode(),      # always "cross"
            "side": "buy" if request.side > 0 else "sell",
            "ordType": "limit" if is_limit else "market",
            "sz": format_qty(self._to_contracts(request.qty), self._filters["step_size"]),
            "clOrdId": request.client_order_id,
            "posSide": "net",                       # one-way / net mode; hedge is 5f
            "reduceOnly": bool(request.reduce_only),
            # tgtCcy intentionally absent: SWAP sz is always in contracts, not quote USDT
        }
        if is_limit:
            params["px"] = format_price(request.price, self._filters["tick_size"])
        return params

    def build_cancel_params(self, client_order_id: str) -> dict:
        return {"instId": self._symbol, "clOrdId": client_order_id}

    # --- perp reconcile ---

    def _fetch_usdt_balance(self) -> float:
        """Fetch OKX account balance and return the USDT availBal (float).

        Quote asset: USDT (SWAP perp settle currency).
        Default-safe: any transport/parse failure returns 0.0 so reconcile is never broken.
        Reuses PATH_ACCOUNT ('/api/v5/account/balance') + iter_balances() from the spot parent.
        """
        try:
            raw = self._transport(self._base, self.PATH_ACCOUNT, "GET", {}, self._signer)
            result = self.unwrap(raw)
            for bal in self.iter_balances(result):
                if bal.get("asset") == "USDT":
                    return float(bal.get("free") or 0)
        except Exception:  # noqa: BLE001 — best-effort; never break reconcile on a balance hiccup
            pass
        return 0.0

    def reconcile_positions(self) -> ReconcileSnapshot:
        """GET /api/v5/account/positions. OKX pos is signed CONTRACTS; convert to base via ct_val.
        Net: posSide=='net' -> one BOTH leg (byte-equivalent). Hedge: posSide 'long'/'short' rows ->
        a LONG leg AND a SHORT leg, each signed-base and carrying its position_side.
        Also fetches /api/v5/account/balance (USDT availBal) -> ReconcileSnapshot.balance.
        Balance fetch is default-safe: failure → 0.0, positions still returned.
        """
        data = self.unwrap(self._transport(
            self._base, self.PATH_POSITIONS, "GET",
            {"instType": "SWAP", "instId": self._symbol}, self._signer))
        bal = self._fetch_usdt_balance()
        legs: list[tuple[str, float, float, float, str]] = []   # (sym, signed_base, avg, mark, side)
        for p in data:
            contracts = float(p.get("pos", 0) or 0)             # already signed
            if contracts == 0.0:
                continue
            side_lbl = _POSSIDE_LABEL.get(str(p.get("posSide", "net")), "BOTH")
            legs.append((self._symbol, self._to_base(contracts),
                         float(p.get("avgPx", 0) or 0),
                         float(p.get("markPx", 0) or 0),
                         side_lbl))
        if not legs:
            return ReconcileSnapshot(
                positions=((self._symbol, 0.0),), open_orders=(),
                position_avg_px=((self._symbol, 0.0),),
                position_mark_px=((self._symbol, 0.0),),
                balance=bal)
        hedge = any(sd != "BOTH" for *_r, sd in legs)
        return ReconcileSnapshot(
            positions=tuple((s, q) for s, q, _a, _m, _sd in legs),
            open_orders=(),
            position_avg_px=tuple((s, a) for s, _q, a, _m, _sd in legs),
            position_mark_px=tuple((s, m) for s, _q, _a, m, _sd in legs),
            position_sides=tuple((s, sd) for s, _q, _a, _m, sd in legs) if hedge else (),
            balance=bal)
