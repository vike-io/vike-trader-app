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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vike_trader_app.core.orders import Order


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


def backtest_order_request(
    *,
    side: int,
    qty: float,
    order_type: str = "market",
    price: float | None = None,
    trigger_price: float | None = None,
    weight: float = 0.0,
    stop: float | None = None,
    trail: float | None = None,
    extreme: float | None = None,
    on_close: bool = False,
    reduce_only: bool = False,
    symbol: str = "",
    coid: str = "",
) -> "OrderRequest":
    """Synthesize a minimal valid ``OrderRequest`` for the backtest path.

    ``client_order_id``, ``venue``, and ``symbol`` are placeholders — they are NOT
    read by ``order_request_to_resting`` (``core.Order`` has no coid/venue/symbol);
    they only matter in rung 3 when the ``SimulatedExchange`` routes the intent.
    """
    return OrderRequest(
        client_order_id=coid,
        venue="",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        price=price,
        trigger_price=trigger_price,
        reduce_only=reduce_only,
        weight=weight,
        stop=stop,
        trail=trail,
        extreme=extreme,
        on_close=on_close,
    )


def order_request_to_resting(req: "OrderRequest") -> "Order":
    """Map a frozen ``OrderRequest`` intent to a live mutable ``core.Order`` resting record.

    Returns a LIVE mutable ``core.Order`` — engines ratchet ``extreme`` and cap ``size``
    in place after submission.  The 6-kind dispatch matches the per-verb field sets the
    backtest engines build today (byte-identical).

    Dispatch priority:
    1. ``trail is not None``                       → trailing
    2. ``on_close=True, order_type="market"``      → market_close
    3. ``on_close=True, order_type="limit"``       → limit_close
    4. ``order_type="stop"``                       → stop
    5. ``order_type="limit"``                      → limit   (carries stop)
    6. else (``order_type="market"``)              → market  (carries stop)
    """
    # Deliberate lazy import: avoids a module-level cycle (order_intent ← orders ← order_intent).
    # orders.py imports core primitives; order_intent.py must not import orders at module level.
    from vike_trader_app.core.orders import Order

    side = req.side
    qty = req.qty

    if req.trail is not None:
        return Order("trailing", side, qty, trail=req.trail, extreme=req.extreme, weight=req.weight)
    if req.on_close and req.order_type == "market":
        return Order("market_close", side, qty, weight=req.weight)
    if req.on_close and req.order_type == "limit":
        return Order("limit_close", side, qty, price=req.price, weight=req.weight)
    if req.order_type == "stop":
        return Order("stop", side, qty, price=req.trigger_price, weight=req.weight)
    if req.order_type == "limit":
        return Order("limit", side, qty, price=req.price, weight=req.weight, stop=req.stop)
    # market (default)
    return Order("market", side, qty, weight=req.weight, stop=req.stop)
