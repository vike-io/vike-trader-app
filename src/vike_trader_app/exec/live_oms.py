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
    FundingEvent,
    OrderAccepted,
    OrderCanceled,
    OrderDenied,
    OrderExpired,
    OrderFilled,
    OrderLiquidated,
    OrderPartiallyFilled,
    OrderRejected,
    OrderRequest,
    OrderSubmitted,
    OrderTriggered,
    PositionLiquidated,
)
from vike_trader_app.exec.order import InvalidOrderTransition, ManagedOrder, OrderStatus
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
        self._seen_trade_ids: set[str] = set()
        self._seen_fsm_trade_ids: set[str] = set()
        self._seen_liq_ids: set[str] = set()
        self.bus.subscribe(self._on_event)

    def submit_ticket(self, request: OrderRequest) -> None:
        """Gate the order; publish OrderDenied on veto (follow-up #1) or submit to the venue."""
        ctx = RiskContext(
            position_size=self._position_size(),
            mark_price=request.price if request.price is not None else self._mark(),
            trading_state=self._trading_state,
            now_ms=self._now_ms(),
        )
        verdict = self.gate.check(request, ctx)
        if not verdict.ok or verdict.request is None:
            self.bus.publish(OrderDenied(client_order_id=request.client_order_id,
                                         reason=verdict.reason, ts=request.ts))
            return
        self.registry[verdict.request.client_order_id] = ManagedOrder(request=verdict.request)
        self.client.submit(verdict.request)

    def cancel_ticket(self, client_order_id: str) -> None:
        """Cancel a live order by client-order-id (mirrors submit_ticket's registry+client ownership).

        Idempotent: no-op if the order is unknown or already terminal. Publishes NOTHING — the venue WS
        user-data stream emits the authoritative OrderCanceled that advances the FSM (live_oms.py
        _on_event -> mo.apply). client.cancel swallows the already-gone case (crypto_client.py:91) and
        re-raises VenueApiError for any other code; the GUI slot wraps that.
        """
        mo = self.registry.get(client_order_id)
        if mo is None or mo.status in (
                OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED,
                OrderStatus.DENIED, OrderStatus.EXPIRED, OrderStatus.LIQUIDATED):
            return                                  # nothing live to cancel
        self.client.cancel(client_order_id)

    def apply_snapshot(self, snapshot) -> None:
        """Seed Account positions (size + avg_px) and the open-order registry from a reconcile snapshot.

        Each position row carries an optional position_side (snapshot.position_sides, index-aligned to
        snapshot.positions; default 'BOTH' when absent). avg_px is looked up per (symbol, side) so a
        hedge LONG and SHORT leg of the same symbol get distinct cost bases. mark is per-symbol (one
        mark feed; both legs mark identically). A net/spot snapshot (no position_sides) writes the
        (venue, sym, 'BOTH') key with the symbol-keyed avg_px -- byte-equivalent to pre-5g-3.
        """
        sides = list(getattr(snapshot, "position_sides", ()))
        avg_by_key = {}                                    # (sym, side) -> avg_px, parallel to positions
        for i, (sym, avg) in enumerate(getattr(snapshot, "position_avg_px", ())):
            side = sides[i][1] if i < len(sides) else "BOTH"
            avg_by_key[(sym, side)] = avg
        for i, (sym, qty) in enumerate(snapshot.positions):
            assert sym == self.symbol, f"snapshot symbol {sym} != hub symbol {self.symbol}"
            side = sides[i][1] if i < len(sides) else "BOTH"
            self.account.positions[(self.venue, sym, side)] = {
                "size": qty,
                "avg_px": avg_by_key.get((sym, side), 0.0),
            }
        for sym, mark in getattr(snapshot, "position_mark_px", ()):
            if mark > 0.0:
                self.account.set_mark(self.venue, sym, mark)
        for mo in snapshot.open_orders:
            self.registry[mo.client_order_id] = mo

    def shutdown(self) -> None:
        """Symmetric detach (follow-up #6, Qt-free half): unsubscribe the bus + detach the client."""
        self.bus.unsubscribe(self._on_event)
        detach = getattr(self.client, "detach", None)
        if callable(detach):
            detach()

    # --- internals ---
    def _position_size(self, position_side: str = "BOTH") -> float:
        """Signed size of one leg (default 'BOTH' -> the one-way/spot leg).

        submit_ticket calls this with the default so its RiskContext.position_size is
        byte-identical to pre-5g-2. In a hedge account the default returns 0.0 (no BOTH leg
        exists); callers that need per-leg sizing pass position_side='LONG' or 'SHORT'.
        """
        pos = self.account.positions.get((self.venue, self.symbol, position_side))
        return pos["size"] if pos is not None else 0.0

    def total_exposure(self) -> float:
        """Gross notional across ALL legs of this hub's symbol at the current mark.

        Sums abs(size)*mark over every position_side present for this (venue, symbol) pair
        (LONG, SHORT, and/or BOTH). A hedge account holds a long AND a short leg simultaneously,
        so this SUMS abs() values — it NEVER nets LONG against SHORT (netting would hide gross
        exposure). A one-way account (single BOTH leg) produces the same result as abs(size)*mark.
        Returns 0.0 if no mark has been recorded yet.

        Note (5g-3 follow-up): submit_ticket's gate uses _position_size() with the default 'BOTH'
        key. In a hedge account this returns 0.0 (no BOTH leg), so the gate would mis-project
        exposure as flat. submit_ticket has ZERO production callers today — threading real
        LONG/SHORT through OrderRequest + RiskContext is 5g-3 / the order-ticket slice.
        """
        mark = self.account.marks.get((self.venue, self.symbol), 0.0)
        return sum(
            abs(pos["size"]) * mark
            for (v, s, _ps), pos in self.account.positions.items()
            if v == self.venue and s == self.symbol
        )

    def _mark(self) -> float:
        """Latest recorded mark for this venue/symbol (0.0 if none yet). Seeds the gate's notional
        valuation for orders that carry no price (perp MARKET orders)."""
        return self.account.marks.get((self.venue, self.symbol), 0.0)

    def _on_event(self, event) -> None:
        if isinstance(event, FillEvent) and event.symbol != self.symbol:
            return  # account-wide WS stream: ignore fills for other symbols (not this hub's order)
        if isinstance(event, FillEvent):
            # In-memory dedup: always-on guard against WS reconnect replays (Fix 1).
            if event.trade_id:
                if event.trade_id in self._seen_trade_ids:
                    return  # reconnect replay — drop
                self._seen_trade_ids.add(event.trade_id)
            # Persistent dedup layer (only when exec_db is wired up).
            if self._exec_db is not None:
                from vike_trader_app.data import exec_db

                fresh = exec_db.record_fill(
                    self._exec_db, trade_id=event.trade_id, client_order_id=event.client_order_id,
                    symbol=event.symbol, side=event.side, qty=event.last_qty, px=event.last_px,
                    commission=event.commission, ts=event.ts)
                if not fresh:
                    return  # reconnect replay — drop exactly once
            self.account.apply_fill(event)
            if event.mark_price is not None and event.mark_price > 0.0:
                self.account.set_mark(event.venue, event.symbol, event.mark_price)
            return
        if isinstance(event, _LIFECYCLE):
            mo = self.registry.get(event.client_order_id)
            if mo is None:
                return
            # FSM-side fill dedup: a reconnect-replayed fill re-emits the OrderFilled/OrderPartiallyFilled
            # wrap. The bare FillEvent above is deduped via _seen_trade_ids (Account stays correct), but the
            # wrap would otherwise re-run _accumulate_fill and double-count the registry's filled_qty/
            # avg_fill_px. Dedup by the fill's trade_id with a SEPARATE set — _seen_trade_ids was already
            # consumed by the preceding bare FillEvent, so it cannot be reused here.
            fill = getattr(event, "fill", None)
            tid = getattr(fill, "trade_id", "") if fill is not None else ""
            if tid and tid in self._seen_fsm_trade_ids:
                return  # reconnect replay — the FSM already advanced for this fill
            try:
                mo.apply(event)
            except InvalidOrderTransition:
                return  # idempotent/out-of-order WS replay — skip (Fix 2)
            if tid:
                self._seen_fsm_trade_ids.add(tid)  # mark seen only after a successful apply
            self._persist_order(mo)
            return
        if isinstance(event, FundingEvent):
            if event.symbol != self.symbol:
                return
            self.account.apply_funding(event)
            self._journal("FundingEvent", event)
            return
        if isinstance(event, PositionLiquidated):
            if event.symbol != self.symbol:
                return
            # Liquidation dedup: a WS reconnect can replay a partial liq frame. Mirror the FillEvent
            # _seen_trade_ids guard (above) — an empty trade_id skips dedup and always applies (the
            # legacy whole-flatten path), a distinct id closes its own clamped qty exactly once.
            if event.trade_id:
                if event.trade_id in self._seen_liq_ids:
                    return  # reconnect replay — drop
                self._seen_liq_ids.add(event.trade_id)
            self.account.apply_liquidation(event)
            coid = self._coid_for_position(event)
            if coid is not None:
                mo = self.registry.get(coid)
                if mo is not None:
                    try:
                        mo.apply(OrderLiquidated(client_order_id=mo.client_order_id,
                                                 liq_price=event.liq_price, ts=event.ts))
                    except InvalidOrderTransition:
                        pass  # already terminal — idempotent replay
                    self._persist_order(mo)
            self._journal("PositionLiquidated", event)
            return

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

    def _coid_for_position(self, ev) -> str | None:
        """Most-recent live order on this symbol/side — the leg a liquidation force-closed.

        One-way (position_side BOTH) keys by symbol only — the EXACT pre-5g-2 predicate
        (byte-equivalent; reversed() scan, first non-terminal wins). Hedge (LONG/SHORT) ALSO
        requires the order's leg (derived from request.side: +1 -> 'LONG', -1 -> 'SHORT') to
        match the event's position_side, so a LONG liquidation advances the LONG order and not
        the SHORT.

        Limitation (5g-3): a reduce_only/flatten order opens against the position direction
        (side=-1 to flatten a long). Its request.side would map it to 'SHORT' even though it
        conceptually owns the LONG leg. In 5g-2 hedge-open orders are side-pure so this
        heuristic is correct; carrying a real position_side on OrderRequest (which would make
        reduce-only flatten orders unambiguous) is deferred to 5g-3.

        Returns None if nothing matches; Account still flattens by key regardless.
        """
        want_side = (
            ev.position_side
            if getattr(ev, "position_side", "BOTH") in ("LONG", "SHORT")
            else None
        )
        for coid, mo in reversed(list(self.registry.items())):
            if mo.request.symbol != ev.symbol or mo.status in (
                    OrderStatus.LIQUIDATED, OrderStatus.FILLED, OrderStatus.CANCELED):
                continue
            if want_side is not None:
                order_leg = "LONG" if mo.request.side > 0 else "SHORT"
                if order_leg != want_side:
                    continue
            return coid
        return None

    def _journal(self, kind: str, event) -> None:
        """Append a perp event to the durable audit trail (reuses exec_events JSON; no schema change)."""
        if self._exec_db is None:
            return
        import json
        from dataclasses import asdict
        from vike_trader_app.data import exec_db
        exec_db.append_event(self._exec_db, ts=getattr(event, "ts", 0), kind=kind,
                             client_order_id=getattr(event, "client_order_id", None),
                             payload=json.dumps(asdict(event)))
