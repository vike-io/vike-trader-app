"""DeribitExecutionClient — JSON-RPC private/buy|sell|cancel over an INJECTED request/response transport.

This is NOT a CryptoExecutionClient subclass (Deribit is JSON-RPC/WS, not REST/HMAC). It exposes the
SAME duck-typed seam LiveOmsHub already calls — submit(OrderRequest), cancel(coid), connect() — and
publishes the SAME bus events (OrderSubmitted/OrderAccepted/OrderRejected) as crypto_client.py:72, so
the live order path is reused, not forked.

transport(method: str, params: dict) -> dict is the parsed JSON-RPC response ({"id","result"} on
success, {"id","error":{code,message}} on failure). In 6a tests inject a fake; the WS-backed real
transport is 6b. amount/price are COIN units (options); post_only is forced False (Deribit defaults it
True — a crossing order would otherwise be rejected/repriced).
"""
from __future__ import annotations

from vike_trader_app.exec.binance.format import format_price, format_qty
from vike_trader_app.exec.crypto_client import ReconcileSnapshot, VenueApiError
from vike_trader_app.exec.deribit.reconcile import build_reconcile_snapshot
from vike_trader_app.exec.deribit.rpc import JsonRpcBuilder, parse_response
from vike_trader_app.exec.events import (
    OrderAccepted,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
)

# Deribit error codes for a cancel of an already-gone order (filled / cancelled / unknown) -> swallow.
# 10004 = order_not_found, 11044 = not_open_order, 10010 = already_closed, 11008 = already_filled
_NOT_FOUND_CODES = frozenset({10004, 11044, 10010, 11008})


class DeribitApiError(VenueApiError):
    """A Deribit JSON-RPC {code, message} error (reuses the shared (code, msg) base)."""


class DeribitExecutionClient:
    """JSON-RPC execution client. Drive with submit()/cancel(); reconcile via connect()."""

    VENUE = "deribit"

    def __init__(self, bus, *, transport, symbol: str, filters: dict, currency: str = "",
                 builder=None) -> None:
        self.bus = bus
        self._transport = transport          # callable(method, params) -> parsed JSON-RPC response
        self._symbol = symbol
        self._filters = filters
        self._currency = currency
        self._builder = builder or JsonRpcBuilder()
        self._order_ids: dict[str, str] = {}

    # --- order-param mapping (pure) ---
    def build_order_params(self, request: OrderRequest) -> dict:
        is_limit = request.order_type.lower() == "limit"
        params: dict = {
            "instrument_name": self._symbol,
            "amount": float(format_qty(request.qty, self._filters["step_size"])),
            "type": "limit" if is_limit else "market",
            "label": request.client_order_id,
            # Deribit defaults post_only=True; a crossing/marketable order MUST set it False.
            "post_only": False,
        }
        if is_limit and request.price is not None:
            params["price"] = float(format_price(request.price, self._filters["tick_size"]))
        if request.reduce_only:
            params["reduce_only"] = True
        return params

    # --- duck-typed seam LiveOmsHub calls ---
    def submit(self, request: OrderRequest) -> None:
        """OrderSubmitted -> private/buy|sell -> OrderAccepted|OrderRejected (mirrors crypto_client.py:72)."""
        self.bus.publish(OrderSubmitted(client_order_id=request.client_order_id, ts=request.ts))
        method = "private/buy" if request.side > 0 else "private/sell"
        resp = self._transport(method, self.build_order_params(request))
        _rid, result, error = parse_response(resp)
        if error is not None:
            self.bus.publish(OrderRejected(client_order_id=request.client_order_id,
                                           reason=str(error.get("message", "")), ts=request.ts))
            return
        order = (result or {}).get("order", {})
        order_id = str(order.get("order_id", ""))
        if order_id:
            self._order_ids[request.client_order_id] = order_id
        self.bus.publish(OrderAccepted(client_order_id=request.client_order_id,
                                       venue_order_id=order_id, ts=request.ts))

    def cancel(self, client_order_id: str) -> None:
        """private/cancel by the recorded venue order_id; swallow not-found, re-raise other errors."""
        order_id = self._order_ids.get(client_order_id)
        if not order_id:
            return  # nothing live to cancel
        resp = self._transport("private/cancel", {"order_id": order_id})
        _rid, _result, error = parse_response(resp)
        if error is not None:
            code = int(error.get("code", 0))
            if code in _NOT_FOUND_CODES:
                return
            raise DeribitApiError(code, str(error.get("message", "")))

    def connect(self) -> ReconcileSnapshot:
        """Reconcile on arm (MAIN thread, after transport.connect()): fetch the armed instrument's
        position + open orders over the authed transport and build a populated ReconcileSnapshot.

        private/get_positions is currency-scoped (every option of self._currency) so the result is
        FILTERED to self._symbol in build_reconcile_snapshot; private/get_open_orders_by_instrument is
        already instrument-scoped, so apply_snapshot's sym == hub.symbol assert (live_oms.py:102) holds
        by construction. Options are one-way -> a single BOTH row (no hedge legs). Raises
        DeribitApiError on any getter error (a bad reconcile must abort the arm before any fill).
        """
        pos_result = self._private_result(
            "private/get_positions", {"currency": self._currency, "kind": "option"})
        ord_result = self._private_result(
            "private/get_open_orders_by_instrument",
            {"instrument_name": self._symbol, "type": "all"})
        return build_reconcile_snapshot(pos_result, ord_result, self._symbol)

    def _private_result(self, method: str, params: dict) -> list:
        """Call a private getter, raise DeribitApiError on error, return the result list (or [])."""
        _rid, result, error = parse_response(self._transport(method, params))
        if error is not None:
            raise DeribitApiError(int(error.get("code", 0)), str(error.get("message", "")))
        return result or []

    def detach(self) -> None:
        """Close the live order transport (the persistent authed order WS) on teardown.

        LiveOmsHub.shutdown() (live_oms.py:117) calls getattr(client, 'detach', None)() — Deribit is the
        FIRST client to use this dormant, pre-wired hook. Tolerant of a transport with no close() (the 6a
        fake injected in unit tests has none): only calls close() if it's callable. Runs on the MAIN
        thread AFTER the fill worker is stop()+wait()-joined (LiveExecutionSession.shutdown), so the
        socket close is bounded + race-free (0xC0000409-safe)."""
        transport = getattr(self, "_transport", None)
        close = getattr(transport, "close", None)
        if callable(close):
            close()
