"""SimulatedExchange — attaches to a SingleSymbolEngine and publishes the full order lifecycle.

This is a PASSIVE MIRROR. The engine remains the canonical source of truth for cash, ``_pending``,
``trades``, and ``equity_curve``. ``SimulatedExchange`` OBSERVES the canonical fill path via the
engine's three default-None hooks (``_on_submit`` / ``_on_fill`` / ``_on_cancel``) and re-publishes
those observations as the full lifecycle event stream that drives the ``ManagedOrder`` FSM registry.

Architecture contract (enforced here):
  1. ``_on_fill`` publishes the bare ``FillEvent`` FIRST so ``Account.apply_fill`` folds it before
     the ``OrderFilled`` / ``OrderPartiallyFilled`` wrapper arrives — the ``Account`` subscriber never
     sees ``OrderFilled.fill``, so there is ZERO double-counting.
  2. No public ``submit()`` / ``cancel()`` door is wired into the engine book in this slice. The
     engine keeps its own ``_pending`` list; the swappable-venue front door belongs to Slice D.
  3. Default-off guards (``_on_submit`` / ``_on_fill`` / ``_on_cancel`` are ``None`` until this
     class attaches) mean zero event work in the optimizer / ProcessPool no-exchange path.

Nominal lifecycle per order:
  OrderSubmitted -> OrderAccepted -> (OrderPartiallyFilled*) -> OrderFilled | OrderCanceled
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vike_trader_app.exec.accounting import Account

from vike_trader_app.core.order_intent import OrderRequest
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import (
    FillEvent,
    FundingEvent,
    OrderAccepted,
    OrderCanceled,
    OrderFilled,
    OrderPartiallyFilled,
    OrderSubmitted,
)
from vike_trader_app.exec.order import ManagedOrder, OrderStatus


class SimulatedExchange:
    """Passive lifecycle mirror: observes ``SingleSymbolEngine`` hooks and drives ``ManagedOrder`` FSMs.

    Parameters
    ----------
    engine:
        A ``SingleSymbolEngine`` instance. Its ``_on_submit``, ``_on_fill``, and ``_on_cancel``
        hooks are overwritten by this constructor. Build before calling ``engine.run()``.
    bus:
        The synchronous ``EventBus`` onto which lifecycle events are published.
    venue:
        Venue label used in minted coids and ``FillEvent.venue``.
    symbol:
        Symbol label used in minted coids and ``FillEvent.symbol``.
    """

    def __init__(self, engine, bus: EventBus, *, venue: str = "sim", symbol: str = "",
                 sim_account: "Account | None" = None) -> None:
        self.engine = engine
        self.bus = bus
        self.venue = venue
        self.symbol = symbol

        # Minted client_order_id counter (monotonic; never reused within a run).
        self._n: int = 0
        # Fill counter (for trade_id uniqueness).
        self._fill_n: int = 0
        # Liquidation counter (for synthetic coid uniqueness when order is None).
        self._liq_n: int = 0

        # id(core.Order) -> minted client_order_id
        self._order_coid: dict[int, str] = {}

        # client_order_id -> ManagedOrder (the FSM registry)
        self.registry: dict[str, ManagedOrder] = {}

        # Funding event counter.
        self._funding_n: int = 0

        # Attach hooks (overwrite; engine hooks are default-None before attachment).
        engine._on_submit = self._on_submit
        engine._on_fill = self._on_fill
        engine._on_cancel = self._on_cancel
        engine._on_funding = self._on_funding

        # Optional decomposed-ledger mirror for single REPORT runs.
        # None on the optimizer/sweep path — zero per-bar cost.
        self.sim_account = sim_account
        if sim_account is not None:
            bus.subscribe(lambda ev: sim_account.apply_fill(ev) if isinstance(ev, FillEvent) else None)
            bus.subscribe(lambda ev: sim_account.apply_funding(ev) if isinstance(ev, FundingEvent) else None)

    # ---------------------------------------------------------------------------
    # Hook implementations
    # ---------------------------------------------------------------------------

    def _on_submit(self, order) -> None:
        """Called by the engine whenever a new order is appended to ``_pending``."""
        # Mint a unique client_order_id.
        coid = f"{self.venue}-{self._n}"
        self._n += 1

        # Map core.Order identity -> coid so _on_fill can look it up by id(order).
        self._order_coid[id(order)] = coid

        # Infer order_type from the kind stored on the resting core.Order.
        kind = order.kind  # "market" | "market_close" | "limit" | "limit_close" | "stop" | "trailing"
        if kind in ("market", "market_close"):
            order_type = "market"
        elif kind in ("limit", "limit_close"):
            order_type = "limit"
        elif kind == "stop":
            order_type = "stop"
        else:
            # "trailing" -> use "stop" as the closest standard order_type
            order_type = "stop"

        # Build the minimal OrderRequest that the ManagedOrder FSM needs.
        req = OrderRequest(
            client_order_id=coid,
            venue=self.venue,
            symbol=self.symbol,
            side=order.side,
            qty=order.size,
            order_type=order_type,
            price=order.price,
            trigger_price=order.price if kind == "stop" else None,
            ts=self.engine._now,
        )
        mo = ManagedOrder(request=req)
        self.registry[coid] = mo

        # Publish OrderSubmitted -> OrderAccepted (INITIALIZED -> SUBMITTED -> ACCEPTED).
        ts = self.engine._now
        ev_sub = OrderSubmitted(client_order_id=coid, ts=ts)
        mo.apply(ev_sub)
        self.bus.publish(ev_sub)

        ev_acc = OrderAccepted(client_order_id=coid, venue_order_id=coid, ts=ts)
        mo.apply(ev_acc)
        self.bus.publish(ev_acc)

    def _on_fill(self, side_sign: int, size: float, price: float, fee: float,
                 ts: int, is_maker: bool, order=None) -> None:
        """Called by the engine (from ``_apply_fill``) on every fill — including liquidations."""
        # Resolve the client_order_id. Liquidation fills arrive with order=None.
        if order is not None:
            coid = self._order_coid.get(id(order))
        else:
            # Synthetic liquidation coid — no ManagedOrder, just a bare FillEvent.
            coid = f"liq-{self._liq_n}"
            self._liq_n += 1

        # Build the bare FillEvent (same shape as SimulatedExecutionClient).
        trade_id = f"{self.venue}-{self._fill_n}"
        self._fill_n += 1
        fill_ev = FillEvent(
            trade_id=trade_id,
            client_order_id=coid if coid is not None else f"liq-{self._liq_n - 1}",
            venue=self.venue,
            symbol=self.symbol,
            side=side_sign,
            last_qty=size,
            last_px=price,
            commission=fee,
            liquidity_side="maker" if is_maker else "taker",
            ts=ts,
        )

        # CRITICAL: publish the bare FillEvent FIRST.
        # The Account subscriber folds this and only this. Publishing OrderFilled AFTER
        # means Account.apply_fill has already run; the OrderFilled.fill reference is
        # NEVER processed by Account -> zero double-count.
        self.bus.publish(fill_ev)

        # Drive the ManagedOrder FSM if we have a tracked order.
        if order is None or coid is None:
            # Liquidation or unknown order — no FSM to update; bare FillEvent is sufficient.
            return

        mo = self.registry.get(coid)
        if mo is None:
            return

        # Guard: only drive FSM from ACCEPTED or PARTIALLY_FILLED.
        # (If somehow an order was never accepted, skip FSM to avoid InvalidOrderTransition.)
        if mo.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            return

        # The original requested qty is in mo.request.qty.
        # After accumulating this fill, decide partial vs. full.
        # We compute the NEW total filled by peeking at what _accumulate_fill would produce.
        new_total = mo.filled_qty + size
        original_qty = mo.request.qty

        if new_total < original_qty - 1e-9:
            # Partial fill: cumulative still below the order size.
            ev_lifecycle = OrderPartiallyFilled(client_order_id=coid, fill=fill_ev, ts=ts)
        else:
            # Full fill (or fill that completes the order, including minor float drift).
            ev_lifecycle = OrderFilled(client_order_id=coid, fill=fill_ev, ts=ts)

        mo.apply(ev_lifecycle)       # accumulates fill + transitions status
        self.bus.publish(ev_lifecycle)

    def _on_funding(self, amount_signed: float, ts: int) -> None:
        """Called by the engine when a funding cashflow is applied (amount_signed = signed cash delta).

        ``amount_signed`` == ``-funding_charge`` so that ``Account.balance += ev.amount`` mirrors
        ``engine.cash -= funding_charge`` exactly (both are the same signed delta applied to cash).
        Publishes a ``FundingEvent`` onto the bus so ``Account.apply_funding`` folds it.
        """
        ev = FundingEvent(
            venue=self.venue,
            symbol=self.symbol,
            position_side="BOTH",
            funding_rate=0.0,          # rate not available at hook call-site; amount is authoritative
            amount=amount_signed,
            ts=ts,
        )
        self._funding_n += 1
        self.bus.publish(ev)

    def _on_cancel(self, order) -> None:
        """Called by the engine whenever an order is removed from ``_pending`` via cancel."""
        coid = self._order_coid.get(id(order))
        if coid is None:
            return

        mo = self.registry.get(coid)
        if mo is None:
            return

        # Guard: only cancel from ACCEPTED or PARTIALLY_FILLED (FSM allows both).
        if mo.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            return

        ts = self.engine._now
        ev = OrderCanceled(client_order_id=coid, ts=ts)
        mo.apply(ev)
        self.bus.publish(ev)
