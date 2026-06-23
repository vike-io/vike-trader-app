"""LiveOmsHub — the Qt-free LIVE composition root (a SIBLING of OmsHub, not a subclass).

Owns bus/account/gate/registry/client. Manual-ticket order path: submit_ticket() calls the gate and
publishes OrderDenied on veto (follow-up #1) or client.submit on ok; there is NO BacktestEngine and
NO OrderRouter-over-engine in the live path. _on_event extends OmsHub's FillEvent-only dispatch:
bare FillEvent -> Account.apply_fill (byte-identical to paper) + exec_db dedup; Order* lifecycle ->
the ManagedOrder registry (the new seam). shutdown() is symmetric: unsubscribe the bus + detach the
client (follow-up #6); the worker stop()/wait() is the LiveExecutionSession's half.
"""

from __future__ import annotations

from vike_trader_app.exec.events import (
    FillEvent,
    OrderAccepted,
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderPartiallyFilled,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
    OrderTriggered,
)
from vike_trader_app.exec.risk import RiskContext, TradingState

_LIFECYCLE = (OrderSubmitted, OrderAccepted, OrderTriggered, OrderPartiallyFilled,
              OrderFilled, OrderCanceled, OrderRejected, OrderExpired, OrderDenied)


class LiveOmsHub:
    """Live composition root. Drive orders with submit_ticket(); read .account / .registry."""

    def __init__(self, *, bus, account, gate, client, venue: str, symbol: str,
                 exec_db_conn=None, now_ms=lambda: 0) -> None:
        self.bus = bus
        self.account = account
        self.gate = gate
        self.client = client
        self.venue = venue
        self.symbol = symbol
        self._exec_db = exec_db_conn
        self._now_ms = now_ms
        self.registry: dict = {}
        self._trading_state = TradingState.ACTIVE
        self.bus.subscribe(self._on_event)

    def submit_ticket(self, request: OrderRequest) -> None:
        """Gate the order; publish OrderDenied on veto (follow-up #1) or submit to the venue."""
        ctx = RiskContext(
            position_size=self._position_size(),
            mark_price=request.price or 0.0,
            trading_state=self._trading_state,
            now_ms=self._now_ms(),
        )
        verdict = self.gate.check(request, ctx)
        if not verdict.ok or verdict.request is None:
            self.bus.publish(OrderDenied(client_order_id=request.client_order_id,
                                         reason=verdict.reason, ts=request.ts))
            return
        self.client.submit(verdict.request)

    def apply_snapshot(self, snapshot) -> None:
        """Seed the Account position (size only — no venue avg_px) and the open-order registry."""
        for sym, qty in snapshot.positions:
            self.account.positions[(self.venue, sym, "BOTH")] = {"size": qty, "avg_px": 0.0}
        for mo in snapshot.open_orders:
            self.registry[mo.client_order_id] = mo

    def shutdown(self) -> None:
        """Symmetric detach (follow-up #6, Qt-free half): unsubscribe the bus + detach the client."""
        self.bus.unsubscribe(self._on_event)
        detach = getattr(self.client, "detach", None)
        if callable(detach):
            detach()

    # --- internals ---
    def _position_size(self) -> float:
        pos = self.account.positions.get((self.venue, self.symbol, "BOTH"))
        return pos["size"] if pos is not None else 0.0

    def _on_event(self, event) -> None:
        if isinstance(event, FillEvent):
            if self._exec_db is not None:
                from vike_trader_app.data import exec_db

                fresh = exec_db.record_fill(
                    self._exec_db, trade_id=event.trade_id, client_order_id=event.client_order_id,
                    symbol=event.symbol, side=event.side, qty=event.last_qty, px=event.last_px,
                    commission=event.commission, ts=event.ts)
                if not fresh:
                    return  # reconnect replay — drop exactly once
            self.account.apply_fill(event)
            return
        if isinstance(event, _LIFECYCLE):
            mo = self.registry.get(event.client_order_id)
            if mo is not None:
                mo.apply(event)
                self._persist_order(mo)

    def _persist_order(self, mo) -> None:
        if self._exec_db is None:
            return
        from vike_trader_app.data import exec_db

        req = mo.request
        exec_db.upsert_order(
            self._exec_db, client_order_id=mo.client_order_id, venue=self.venue, symbol=req.symbol,
            side=req.side, qty=req.qty, order_type=req.order_type, status=mo.status.value,
            price=req.price, trigger_price=req.trigger_price, venue_order_id=mo.venue_order_id,
            filled_qty=mo.filled_qty, avg_fill_px=mo.avg_fill_px, updated_ts=self._now_ms())
