"""Map live exec EventBus events onto a Strategy's A1 granular handlers (the granular live FSM).

Subscribe AFTER LiveOmsHub so the hub updates Account/FSM first, then the strategy handler fires with
settled state (matching backtest's on_order_filled-after-position-update). Wire events map to core
types (Fill/Position); the strategy never sees exec wire types except via the on_event catch-all.
"""

import logging

from ..core.model import Fill
from . import events as ev

log = logging.getLogger(__name__)


def _fill(fe: "ev.FillEvent") -> Fill:
    return Fill(side=fe.side, size=fe.last_qty, price=fe.last_px, fee=fe.commission, ts=fe.ts,
                is_maker=(fe.liquidity_side == "maker"), symbol=fe.symbol)


class StrategyEventAdapter:
    def __init__(self, strategy, bus) -> None:
        self._s = strategy
        self._bus = bus
        bus.subscribe(self._on_event)

    def unsubscribe(self) -> None:
        self._bus.unsubscribe(self._on_event)

    def _on_event(self, e) -> None:
        try:
            self._dispatch(e)
        except Exception:  # noqa: BLE001 - a strategy handler must not break the bus drain
            log.exception("strategy event handler failed for %s", type(e).__name__)

    def _dispatch(self, e) -> None:
        s = self._s
        t = type(e)
        if t is ev.OrderSubmitted:
            s.on_order_submitted(e)
        elif t is ev.OrderAccepted:
            s.on_order_accepted(e)
        elif t is ev.OrderRejected or t is ev.OrderDenied:
            s.on_order_rejected(e)
        elif t is ev.OrderCanceled:
            s.on_order_canceled(e)
        elif t is ev.OrderExpired:
            s.on_order_expired(e)
        elif t is ev.OrderFilled or t is ev.OrderPartiallyFilled:
            s.on_order_filled(_fill(e.fill))
        # (position + perp events added in Task 2)
        s.on_event(e)
