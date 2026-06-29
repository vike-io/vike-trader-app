"""Strategy-facing handle to a placed order.

The mutable resting record stays inside the engine/OMS (locked rung-2 rule);
the strategy holds only this handle.  Full ``.modify()`` + reliable post-fill
``.filled_qty`` land with rung 3 (P0) / P2; P1 ships ``.id`` + ``.cancel()``
+ best-effort ``working``/``done``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_next_id: int = 0


def _alloc_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


@dataclass
class OrderHandle:
    """Opaque handle returned by every ``submit*`` engine verb.

    The strategy MAY hold this to cancel a specific resting order or inspect
    its status.  It does NOT expose the engine's internal ``Order`` record
    directly (rung-2 rule: the strategy must not mutate engine state).

    Attributes
    ----------
    id:
        Monotonically increasing integer, unique per process lifetime.
    _order:
        The engine's internal ``Order`` object (opaque to the strategy).
    _engine:
        Reference to the ``PortfolioEngine`` that owns this order.
    symbol:
        The symbol the order was placed for.
    """

    id: int
    _order: Any      # engine's internal Order — opaque to the strategy
    _engine: Any     # PortfolioEngine — kept private
    symbol: str

    @property
    def status(self) -> str:
        """``'working'`` while the order is still resting in the engine's
        pending list; ``'done'`` once it has been filled or cancelled."""
        return (
            "working"
            if self._order in self._engine._pending_of(self.symbol)
            else "done"
        )

    @property
    def filled(self) -> bool:
        """``True`` when the order is no longer resting (filled OR cancelled).

        Note: P1 cannot distinguish fill from cancel; that distinction lands
        in a later rung once the engine publishes fill events.
        """
        return self.status == "done"

    def cancel(self) -> None:
        """Remove the order from the engine's pending list (no-op if already gone)."""
        self._engine.cancel_order(self.symbol, self._order)
        logger.debug("OrderHandle %d cancelled for %s", self.id, self.symbol)
