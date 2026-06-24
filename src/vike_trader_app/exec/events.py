"""Immutable order/fill/position events + the OrderRequest intent value object.

Every type is a frozen dataclass with hashable fields only (tuples, never dicts/lists) so events
are copyable and Qt-metatype-safe when marshalled across the thread boundary in later phases. The
``client_order_id`` is the locally-generated idempotency key correlating an intent to its lifecycle.
Reserved members (``FundingEvent``, ``PositionLiquidated``, the contingency slots on ``OrderRequest``,
the ``mark_price`` fields) are defined-but-unwired so Phase-5 perps/OCO are additive, not migrations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderRequest:
    """A submission intent — what a strategy / manual ticket / (later) bracket builds.

    ``side`` is +1 buy / -1 sell. ``order_type`` in {market, limit, stop}; ``price`` is the limit
    price, ``trigger_price`` the stop trigger. The contingency slots are RESERVED for OCO/brackets
    (Phase 5) — present now so linkage is additive wiring, never a schema migration.
    """

    client_order_id: str
    venue: str
    symbol: str                       # canonical symbol (resolver maps to venue symbol at the edge)
    side: int
    qty: float
    order_type: str
    price: float | None = None
    trigger_price: float | None = None
    reduce_only: bool = False
    ts: int = 0
    # --- reserved contingency (OCO/brackets, Phase 5) ---
    parent_order_id: str | None = None
    linked_order_ids: tuple[str, ...] = ()
    order_list_id: str | None = None
    contingency_type: str | None = None   # 'OTO' | 'OCO' | 'OUO' later


@dataclass(frozen=True)
class FillEvent:
    """One execution (partial or full). ``mark_price`` reserved for perp funding/liq pricing."""

    trade_id: str                     # venue execution id — the dedup key across reconnects
    client_order_id: str
    venue: str
    symbol: str
    side: int
    last_qty: float
    last_px: float
    commission: float = 0.0
    liquidity_side: str = ""          # 'maker' | 'taker'
    ts: int = 0
    mark_price: float | None = None
    position_side: str = "BOTH"      # 'BOTH' one-way/spot | 'LONG' | 'SHORT' (hedge perps)


# --- order lifecycle events ---------------------------------------------------------------------

@dataclass(frozen=True)
class OrderSubmitted:
    client_order_id: str
    ts: int = 0


@dataclass(frozen=True)
class OrderAccepted:
    client_order_id: str
    venue_order_id: str | None = None
    ts: int = 0


@dataclass(frozen=True)
class OrderRejected:
    client_order_id: str
    reason: str = ""                  # MultiCharts CalcReason-style discriminator
    ts: int = 0


@dataclass(frozen=True)
class OrderDenied:
    """RiskGate veto (pre-venue) — an event, never a modal (no-modals rule)."""

    client_order_id: str
    reason: str = ""
    ts: int = 0


@dataclass(frozen=True)
class OrderTriggered:
    """A venue-side conditional (stop) fired — distinct from the subsequent fill."""

    client_order_id: str
    ts: int = 0


@dataclass(frozen=True)
class OrderPartiallyFilled:
    client_order_id: str
    fill: FillEvent
    ts: int = 0


@dataclass(frozen=True)
class OrderFilled:
    client_order_id: str
    fill: FillEvent
    ts: int = 0


@dataclass(frozen=True)
class OrderCanceled:
    client_order_id: str
    reason: str = ""
    ts: int = 0


@dataclass(frozen=True)
class OrderExpired:
    client_order_id: str
    ts: int = 0


@dataclass(frozen=True)
class OrderLiquidated:
    """Venue force-close of an order (perp liquidation) — FSM counterpart of PositionLiquidated."""

    client_order_id: str
    liq_price: float = 0.0
    ts: int = 0


# --- position / account (derived from fills) ----------------------------------------------------

@dataclass(frozen=True)
class PositionOpened:
    venue: str
    symbol: str
    position_side: str                # 'BOTH' (one-way/spot) | 'LONG' | 'SHORT' (hedge perps)
    qty: float
    avg_px: float
    ts: int = 0
    mark_price: float | None = None


@dataclass(frozen=True)
class PositionChanged:
    venue: str
    symbol: str
    position_side: str
    qty: float
    avg_px: float
    realized_pnl: float = 0.0
    ts: int = 0
    mark_price: float | None = None


@dataclass(frozen=True)
class PositionClosed:
    venue: str
    symbol: str
    position_side: str
    realized_pnl: float = 0.0
    ts: int = 0


@dataclass(frozen=True)
class AccountState:
    venue: str
    balances: tuple[tuple[str, float], ...] = ()   # (asset, qty) pairs — immutable/hashable
    ts: int = 0


# --- RESERVED (Phase-5 perps; defined-but-unwired so perps are additive) -------------------------

@dataclass(frozen=True)
class FundingEvent:
    venue: str
    symbol: str
    position_side: str
    funding_rate: float
    amount: float
    mark_price: float | None = None
    ts: int = 0


@dataclass(frozen=True)
class PositionLiquidated:
    venue: str
    symbol: str
    position_side: str
    qty: float
    liq_price: float
    fee: float = 0.0
    ts: int = 0
