"""``OrderRequest`` — the frozen submission intent value object.

Moved here from ``exec/events.py`` so ``core`` engines can produce it without a
layering inversion (``core`` must not depend on ``exec``).  The class is re-exported
from ``exec/events.py`` so every existing importer keeps working transparently.

The five trailing fields (``weight`` / ``stop`` / ``trail`` / ``extreme`` / ``on_close``)
are **backtest-only additive fields**: they are defaulted so positional construction and
all live callers are unaffected.  The live path ignores them; the backtest path uses them
to carry per-verb context through ``order_request_to_resting`` (Task B2).

No imports from ``exec`` here — this is a pure dataclass module.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderRequest:
    """A submission intent — what a strategy / manual ticket / (later) bracket builds.

    ``side`` is +1 buy / -1 sell.  ``order_type`` in {market, limit, stop};
    ``price`` is the limit price, ``trigger_price`` the stop trigger.  The contingency
    slots are RESERVED for OCO/brackets (Phase 5) — present now so linkage is additive
    wiring, never a schema migration.

    Backtest-only additive fields (live path ignores them; defaulted → no change to
    positional construction or existing callers):

    * ``weight``   — sizing weight forwarded to ``core.Order``
    * ``stop``     — stop-loss price forwarded to ``core.Order``
    * ``trail``    — trailing-stop distance
    * ``extreme``  — trailing-stop reference price (captured at verb-call time)
    * ``on_close`` — True for *_close verbs (market_close / limit_close kinds)
    """

    client_order_id: str
    venue: str
    symbol: str                        # canonical symbol (resolver maps to venue symbol at the edge)
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
    contingency_type: str | None = None    # 'OTO' | 'OCO' | 'OUO' later
    # --- backtest-only additive fields (live path ignores; defaulted → transparent) ---
    weight: float = 0.0
    stop: float | None = None
    trail: float | None = None
    extreme: float | None = None
    on_close: bool = False
