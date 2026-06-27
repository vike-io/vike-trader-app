"""The pre-trade RiskGate — one extensible Qt-free stage every order crosses.

`check(request, ctx)` returns a `RiskVerdict`: either `ok` with a NORMALIZED (tick/lot-rounded)
`OrderRequest`, or a denial with a `reason`. The gate is PURE — it owns no bus and publishes nothing;
the OmsHub/OrderRouter calls it in all three modes (backtest / paper / live) and acts on the verdict, so
risk rules run identically everywhere. (A vetoed order is currently dropped silently by the caller;
emitting the `OrderDenied` event on veto is reserved for Phase 3b's live order lifecycle.) Five capabilities: instrument
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
    max_leverage: float | None = None            # arm-clamp knob; NOT evaluated in check()
    block_reduce_only_overshoot: bool = False    # reject a reduce_only order exceeding abs(pos)


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


def clamp_leverage(requested: float, max_leverage: float | None) -> float:
    """Pure: clamp the requested leverage to max_leverage (>=1.0). None => no cap."""
    lev = max(1.0, float(requested))
    if max_leverage is not None:
        lev = min(lev, max(1.0, float(max_leverage)))
    return lev


class RiskGate:
    """Pre-trade gate. `check` returns a verdict; the caller acts on it. (A vetoed order is dropped
    today; emitting `OrderDenied` on veto is reserved for Phase 3b's live order lifecycle.)

    One RiskGate per venue/account session; the throttle window is shared across ALL symbols
    routed through it (it is a session-level order-rate limit, not per-symbol).
    """

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self._order_times: deque[int] = deque()

    @staticmethod
    def _reduces(request: OrderRequest, ctx: RiskContext) -> bool:
        """True if the order shrinks abs(position): explicit reduce_only, or opposite a non-zero pos."""
        if request.reduce_only:
            return True
        return ctx.position_size != 0.0 and (request.side * ctx.position_size) < 0.0

    def check(self, request: OrderRequest, ctx: RiskContext) -> RiskVerdict:
        lim = self.limits

        # --- side validation — must be exactly +1 or -1; anything else corrupts exposure math ---
        if request.side not in (1, -1):
            return RiskVerdict(False, None, "invalid-side")

        # --- trading state (kill switch) — runs before anything else ---
        if ctx.trading_state is TradingState.HALTED:
            return RiskVerdict(False, None, "halted")
        if ctx.trading_state is TradingState.REDUCING and not self._reduces(request, ctx):
            return RiskVerdict(False, None, "reduce-only")

        # --- reduce-only overshoot (perp; defense-in-depth + honest local OrderDenied) ---
        if lim.block_reduce_only_overshoot and request.reduce_only \
                and abs(ctx.position_size) < abs(request.qty):
            return RiskVerdict(False, None, "reduce-only-overshoot")

        # --- normalize: round price to tick, size to lot ---
        price = None if request.price is None else _round_to(request.price, lim.tick_size)
        qty = _round_to(request.qty, lim.lot_size)
        req = replace(request, price=price, qty=qty)

        # --- validity ---
        if req.qty <= 0.0:
            return RiskVerdict(False, None, "non-positive-size")
        # stop orders carry price=None + trigger_price; use trigger before falling back to mark
        ref_price = req.price if req.price is not None else (
            req.trigger_price if req.trigger_price is not None else ctx.mark_price)
        notional = abs(req.qty) * ref_price
        if lim.min_notional is not None and notional < lim.min_notional:
            return RiskVerdict(False, None, "below-min-notional")

        # --- per-order notional cap ---
        if lim.max_notional_per_order is not None and notional > lim.max_notional_per_order:
            return RiskVerdict(False, None, "over-max-notional")

        # --- projected exposure cap ---
        # Exposure is valued at ctx.mark_price (position-level mark); per-order notional uses the
        # order's own ref_price above — intentional, not an inconsistency.
        if lim.max_total_exposure is not None:
            projected = abs(ctx.position_size + req.side * req.qty) * ctx.mark_price
            if projected > lim.max_total_exposure:
                return RiskVerdict(False, None, "over-max-exposure")

        # --- sliding-window throttle (only accepted orders consume a slot) ---
        if lim.max_orders_per_window is not None:
            cutoff = ctx.now_ms - lim.window_ms
            while self._order_times and self._order_times[0] <= cutoff:
                self._order_times.popleft()
            if len(self._order_times) >= lim.max_orders_per_window:
                return RiskVerdict(False, None, "rate-limited")
            self._order_times.append(ctx.now_ms)

        return RiskVerdict(True, req, "")
