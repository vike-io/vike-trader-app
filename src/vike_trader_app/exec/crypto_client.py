"""Venue-agnostic crypto spot execution base + the shared reconcile snapshot and error type.

CryptoExecutionClient (added in Task 2) owns the FLOW (every bus.publish + the transport call)
and delegates five venue seams to hooks. ReconcileSnapshot and VenueApiError live here so both
Binance and Bybit subclasses share them. BinanceApiError/BybitApiError subclass VenueApiError so
the shared submit()/cancel() try/except is identical across venues (Binance raises in the transport
on HTTP-4xx; Bybit raises in unwrap() on a 200-body retCode!=0).
"""

from __future__ import annotations

from dataclasses import dataclass

from vike_trader_app.exec.events import OrderAccepted, OrderRejected, OrderRequest, OrderSubmitted
from vike_trader_app.exec.order import ManagedOrder, OrderStatus


class VenueApiError(RuntimeError):
    """A venue order/account error, normalized to (code, msg). Subclassed per venue."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(f"venue error {code}: {msg}")
        self.code = code
        self.msg = msg


@dataclass(frozen=True)
class ReconcileSnapshot:
    positions: tuple[tuple[str, float], ...] = ()
    open_orders: tuple[ManagedOrder, ...] = ()
    # Per-position mark price at reconcile time — seeded as avg_px so an immediate close is ~0 PnL
    # instead of garbage (true cost basis is unknown for a pre-existing holding).
    position_avg_px: tuple[tuple[str, float], ...] = ()


class CryptoExecutionClient:
    """Venue-agnostic spot REST client (ACK-only). Subclasses override the five seam hooks."""

    VENUE: str = ""
    PATH_ORDER_CREATE: str = ""
    PATH_ORDER_CANCEL: str = ""
    PATH_OPEN_ORDERS: str = ""
    PATH_ACCOUNT: str = ""
    PATH_TICKER: str = ""
    CREATE_METHOD: str = "POST"
    CANCEL_METHOD: str = "POST"  # subclasses set per venue (Binance→"DELETE", Bybit→"POST")
    # When True, iter_balances yields the TOTAL balance (locked-sell already included), so the
    # base connect() must NOT add locked_sell_qty again.  Bybit UNIFIED walletBalance is a total;
    # Binance "free" is the free portion only (locked-sell must be added back → False).
    BALANCE_IS_TOTAL: bool = False

    def __init__(self, bus, *, signer, rest_base_url: str, symbol: str, filters: dict,
                 base_asset: str = "", transport, public_transport=None) -> None:
        self.bus = bus
        self._signer = signer
        self._base = rest_base_url
        self._symbol = symbol
        self._filters = filters
        self._base_asset = base_asset
        self._transport = transport
        self._public_transport = public_transport

    # --- shared flow (never overridden) ---
    def submit(self, request: OrderRequest) -> None:
        self.bus.publish(OrderSubmitted(client_order_id=request.client_order_id, ts=request.ts))
        params = self.build_order_params(request)
        try:
            resp = self.unwrap(self._transport(self._base, self.PATH_ORDER_CREATE,
                                                self.CREATE_METHOD, params, self._signer))
        except VenueApiError as exc:
            self.bus.publish(OrderRejected(client_order_id=request.client_order_id,
                                           reason=exc.msg, ts=request.ts))
            return
        self.bus.publish(OrderAccepted(client_order_id=request.client_order_id,
                                       venue_order_id=self.parse_venue_order_id(resp),
                                       ts=request.ts))

    def cancel(self, client_order_id: str) -> None:
        try:
            self.unwrap(self._transport(self._base, self.PATH_ORDER_CANCEL, self.CANCEL_METHOD,
                                        self.build_cancel_params(client_order_id), self._signer))
        except VenueApiError as exc:
            if self.is_order_not_found(exc.code):
                return
            raise

    def connect(self) -> ReconcileSnapshot:
        account = self.unwrap(self._transport(self._base, self.PATH_ACCOUNT, "GET",
                                              self.build_account_params(), self._signer))
        free = 0.0
        for bal in self.iter_balances(account):
            if bal["asset"] == self._base_asset:
                free = float(bal["free"] or 0)
                break

        raw = self.unwrap(self._transport(self._base, self.PATH_OPEN_ORDERS, "GET",
                                          self.build_open_orders_params(), self._signer))
        locked_sell_qty = 0.0
        orders: list[ManagedOrder] = []
        for o in self.iter_open_orders(raw):
            if o["side"] < 0:
                locked_sell_qty += max(0.0, o["orig_qty"] - o["executed_qty"])
            req = OrderRequest(
                client_order_id=o["coid"], venue=self.VENUE, symbol=self._symbol,
                side=o["side"], qty=o["orig_qty"], order_type=o["order_type"], price=o["price"])
            orders.append(ManagedOrder(request=req, status=OrderStatus.ACCEPTED,
                                       venue_order_id=o["venue_order_id"]))

        seeded_size = free if self.BALANCE_IS_TOTAL else (free + locked_sell_qty)
        raw_ticker = self._public_transport(self._base, self.PATH_TICKER, self.build_ticker_params())
        mark_px = self.parse_mark_px(self.unwrap(raw_ticker))
        return ReconcileSnapshot(positions=((self._symbol, seeded_size),),
                                 open_orders=tuple(orders),
                                 position_avg_px=((self._symbol, mark_px),))

    # --- venue hooks (override in subclasses) ---
    def build_order_params(self, request: OrderRequest) -> dict:
        raise NotImplementedError

    def build_cancel_params(self, client_order_id: str) -> dict:
        raise NotImplementedError

    def build_account_params(self) -> dict:
        raise NotImplementedError

    def build_open_orders_params(self) -> dict:
        raise NotImplementedError

    def build_ticker_params(self) -> dict:
        raise NotImplementedError

    def parse_venue_order_id(self, resp) -> str:
        raise NotImplementedError

    def iter_balances(self, account_resp):
        raise NotImplementedError

    def iter_open_orders(self, resp):
        raise NotImplementedError

    def parse_mark_px(self, ticker_resp) -> float:
        raise NotImplementedError

    def is_order_not_found(self, code: int) -> bool:
        raise NotImplementedError

    def unwrap(self, resp):
        return resp
