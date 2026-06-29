"""Shared intrabar fill-resolution logic (adverse-first ordering + SL/TP bracket cap).

Extracted verbatim from ``BacktestEngine._resolve_intrabar`` so that both
``BacktestEngine`` and (later) ``PortfolioEngine`` call the *same* component —
the foundation for the D5/D7 engine collapse.

The public entry-point is :func:`resolve_intrabar_fills`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orders import Order


def resolve_intrabar_fills(
    triggered: "list[tuple[Order, float]]",
    position_size: float,
) -> "tuple[list[tuple[Order, float]], int]":
    """Apply adverse-first ordering and SL+TP bracket cap to a list of triggered orders.

    Several resting orders triggered in ONE bar — OHLC can't reveal the intrabar sequence.

    Apply ADVERSE (stop/trailing) fills before FAVOURABLE (limit) fills (pessimistic ordering).
    When more than one order REDUCES the current position in the same bar (a stop-loss +
    take-profit bracket), cap the total reduction to the position size, adverse-first, so the
    profit target can't also fill after the stop already flattened the position. The ambiguous
    bar is counted in the returned ``both_hit`` integer (surfaced on the Result for honesty).

    Parameters
    ----------
    triggered:
        List of ``(order, fill_price)`` pairs — all orders that triggered in this bar.
    position_size:
        The current position size *before* any of these fills are applied (``position.size``).

    Returns
    -------
    resolved:
        The same list, sorted adverse-first, with bracket-capped ``order.size`` values mutated
        in place (identical to the engine's original behaviour).
    both_hit:
        1 if the SL+TP bracket cap was applied this bar, 0 otherwise.  The caller accumulates
        these into ``engine.intrabar_both_hit``.
    """
    triggered = sorted(triggered, key=lambda t: 0 if t[0].kind in ("stop", "trailing") else 1)
    pos = position_size
    closing_side = -1 if pos > 0 else (1 if pos < 0 else 0)
    both_hit = 0
    if closing_side:
        reducers = [t for t in triggered if t[0].side == closing_side]
        has_stop = any(t[0].kind in ("stop", "trailing") for t in reducers)
        has_limit = any(t[0].kind not in ("stop", "trailing") for t in reducers)
        if len(reducers) > 1 and has_stop and has_limit:
            both_hit = 1
            remaining = abs(pos)
            for o, _fp in reducers:          # adverse-first (triggered is already sorted)
                take = min(o.size, remaining)
                o.size = take                # order is consumed this bar -> safe to mutate
                remaining -= take
    return triggered, both_hit
