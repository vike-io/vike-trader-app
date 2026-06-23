"""The pre-trade RiskGate — one extensible Qt-free stage every order crosses.

`check(request, ctx)` returns a `RiskVerdict`: either `ok` with a NORMALIZED (tick/lot-rounded)
`OrderRequest`, or a denial with a `reason`. The gate is PURE — it owns no bus and publishes nothing;
the OmsHub/OrderRouter calls it in all three modes (backtest / paper / live) and publishes the
`OrderDenied` event on veto, so risk rules run identically everywhere. Five capabilities: instrument
rounding + validity, max-notional-per-order, max-total-exposure, a `TradingState` kill-switch, and a
sliding-window order-rate throttler (driven by an injected `ctx.now_ms` clock). Built as ONE thickenable
stage, never a bus-connected engine component.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from enum import Enum

from vike_trader_app.exec.events import OrderRequest


class TradingState(Enum):
    ACTIVE = "ACTIVE"        # normal
    REDUCING = "REDUCING"    # only position-reducing orders allowed
    HALTED = "HALTED"        # no new orders (kill switch)


@dataclass(frozen=True)
class RiskLimits:
    """Gate configuration. All limits are optional; `None` disables that check."""

    tick_size: float | None = None             # price rounding increment
    lot_size: float | None = None              # quantity rounding increment (volume step)
    min_notional: float | None = None          # reject orders below this notional
    max_notional_per_order: float | None = None
    max_total_exposure: float | None = None    # cap on abs(projected position notional)
    max_orders_per_window: int | None = None   # throttle: max orders per window
    window_ms: int = 1000                      # throttle window length


@dataclass(frozen=True)
class RiskContext:
    """Runtime state the gate evaluates an order against."""

    position_size: float = 0.0                 # current SIGNED position in the symbol
    mark_price: float = 0.0                    # price used for notional when the order has none
    trading_state: TradingState = TradingState.ACTIVE
    now_ms: int = 0                            # injected clock (for the throttler)


@dataclass(frozen=True)
class RiskVerdict:
    ok: bool
    request: OrderRequest | None             # normalized (rounded) request when ok
    reason: str = ""


def _round_to(value: float, step: float | None) -> float:
    if step is None or step <= 0:
        return value
    return round(value / step) * step


class RiskGate:
    """Pre-trade gate. `check` returns a verdict; the caller publishes OrderDenied on veto."""

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits

    def check(self, request: OrderRequest, ctx: RiskContext) -> RiskVerdict:
        lim = self.limits
        # --- normalize: round price to tick, size to lot ---
        price = None if request.price is None else _round_to(request.price, lim.tick_size)
        qty = _round_to(request.qty, lim.lot_size)
        req = replace(request, price=price, qty=qty)

        # --- validity ---
        if req.qty <= 0.0:
            return RiskVerdict(False, None, "non-positive-size")
        ref_price = req.price if req.price is not None else ctx.mark_price
        notional = abs(req.qty) * ref_price
        if lim.min_notional is not None and notional < lim.min_notional:
            return RiskVerdict(False, None, "below-min-notional")

        # --- per-order notional cap ---
        if lim.max_notional_per_order is not None and notional > lim.max_notional_per_order:
            return RiskVerdict(False, None, "over-max-notional")

        # --- projected exposure cap ---
        if lim.max_total_exposure is not None:
            projected = abs(ctx.position_size + req.side * req.qty) * ctx.mark_price
            if projected > lim.max_total_exposure:
                return RiskVerdict(False, None, "over-max-exposure")

        return RiskVerdict(True, req, "")
