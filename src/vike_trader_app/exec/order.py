"""The OrderStatus FSM and the ManagedOrder aggregate.

``ManagedOrder.apply(event)`` is the ONLY mutator of order state: it looks the event type up in a
transition table, raises ``InvalidOrderTransition`` on an illegal edge (auditable, Qt-free), and
accumulates fills. ``TRIGGERED`` is first-class (v1 stops are venue-side conditional orders).
``PARTIALLY_FILLED -> CANCELED`` and ``ACCEPTED -> CANCELED`` are allowed so a server-side OCO
sibling-cancel of a partially-filled leg does not raise (stress-test #2 hardening). ``LIQUIDATED`` /
``EMULATED`` / ``RELEASED`` are reserved enum slots for Phase-5 perps / emulated conditionals.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from vike_trader_app.exec.events import (
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


class OrderStatus(Enum):
    INITIALIZED = "INITIALIZED"
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    TRIGGERED = "TRIGGERED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"               # terminal
    CANCELED = "CANCELED"           # terminal
    REJECTED = "REJECTED"           # terminal (venue reject)
    DENIED = "DENIED"               # terminal (RiskGate veto, pre-venue)
    EXPIRED = "EXPIRED"             # terminal
    PENDING_CANCEL = "PENDING_CANCEL"
    # --- reserved (Phase 5) ---
    LIQUIDATED = "LIQUIDATED"       # perp force-close (distinct from CANCELED/FILLED)
    EMULATED = "EMULATED"           # local conditional held client-side
    RELEASED = "RELEASED"           # emulated conditional released to the venue


class InvalidOrderTransition(Exception):
    """Raised when ``apply`` receives an event illegal for the order's current status."""

    def __init__(self, client_order_id: str, status: OrderStatus, event_name: str) -> None:
        super().__init__(f"{client_order_id}: cannot apply {event_name} in {status.name}")
        self.client_order_id = client_order_id
        self.status = status
        self.event_name = event_name


_S = OrderStatus
# event type -> (allowed-from states, resulting state)
_TRANSITIONS: dict[type, tuple[frozenset[OrderStatus], OrderStatus]] = {
    OrderSubmitted: (frozenset({_S.INITIALIZED}), _S.SUBMITTED),
    OrderAccepted: (frozenset({_S.SUBMITTED}), _S.ACCEPTED),
    OrderRejected: (frozenset({_S.INITIALIZED, _S.SUBMITTED}), _S.REJECTED),
    OrderDenied: (frozenset({_S.INITIALIZED}), _S.DENIED),
    OrderTriggered: (frozenset({_S.ACCEPTED}), _S.TRIGGERED),
    OrderPartiallyFilled: (
        frozenset({_S.ACCEPTED, _S.TRIGGERED, _S.PARTIALLY_FILLED}), _S.PARTIALLY_FILLED),
    OrderFilled: (
        frozenset({_S.ACCEPTED, _S.TRIGGERED, _S.PARTIALLY_FILLED}), _S.FILLED),
    OrderCanceled: (
        frozenset({_S.ACCEPTED, _S.TRIGGERED, _S.PARTIALLY_FILLED, _S.PENDING_CANCEL}), _S.CANCELED),
    OrderExpired: (
        frozenset({_S.ACCEPTED, _S.TRIGGERED, _S.PARTIALLY_FILLED}), _S.EXPIRED),
}


@dataclass
class ManagedOrder:
    """An order under management — the request plus its lifecycle state.

    State changes ONLY via ``apply``. ``filled_qty``/``avg_fill_px`` are derived from the fill stream.
    """

    request: OrderRequest
    status: OrderStatus = OrderStatus.INITIALIZED
    venue_order_id: str | None = None
    filled_qty: float = 0.0
    avg_fill_px: float = 0.0

    @property
    def client_order_id(self) -> str:
        return self.request.client_order_id

    def apply(self, event) -> None:
        entry = _TRANSITIONS.get(type(event))
        if entry is None:
            raise InvalidOrderTransition(
                self.client_order_id, self.status, type(event).__name__)
        allowed, to_status = entry
        if self.status not in allowed:
            raise InvalidOrderTransition(
                self.client_order_id, self.status, type(event).__name__)
        if isinstance(event, OrderAccepted) and event.venue_order_id is not None:
            self.venue_order_id = event.venue_order_id
        if isinstance(event, (OrderPartiallyFilled, OrderFilled)):
            self._accumulate_fill(event.fill)
        self.status = to_status

    def _accumulate_fill(self, fill) -> None:
        prev = self.filled_qty
        new = prev + fill.last_qty
        if new > 0:
            self.avg_fill_px = (self.avg_fill_px * prev + fill.last_px * fill.last_qty) / new
        self.filled_qty = new
