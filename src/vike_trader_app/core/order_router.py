"""OrderRouter — the single seam a Strategy submits through, in backtest / paper / live.

A Strategy normally binds to its engine directly. Binding it to an `OrderRouter` instead routes every
ORDER through an optional `RiskGate` first (so the same pre-trade rules — rounding, notional/exposure
caps, kill-switch, throttle — apply identically in backtest, paper, and live), while READS
(`position`, `equity_now`, MTF) forward straight to the engine. `gate=None` is a transparent
pass-through, byte-identical to using the engine directly. Only OPENING entries are gated; closes,
targets, and cancels pass through so a position can always be managed. The router satisfies the
`StrategyEngine` Protocol, so it is a drop-in for the engine wherever a Strategy binds.
"""

from __future__ import annotations

import itertools
from typing import Callable

from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.risk import RiskContext, RiskGate


class OrderRouter:
    """Wrap ``engine``; route opening orders through ``gate`` (or pass through when ``gate`` is None).

    For a non-BacktestEngine engine that lacks `_price`/`_now`, pass `mark_price_fn`/`now_fn` accessors;
    the default falls back to BacktestEngine's internals.
    """

    def __init__(
        self,
        engine,
        gate: RiskGate | None = None,
        *,
        venue: str = "sim",
        symbol: str = "",
        mark_price_fn: Callable[[], float] | None = None,
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._engine = engine
        self._gate = gate
        self._venue = venue
        self._symbol = symbol
        self._mark_price_fn = mark_price_fn
        self._now_fn = now_fn
        self._seq = itertools.count()

    # --- reads: forward straight to the engine ---
    @property
    def position(self):
        return self._engine.position

    @property
    def now(self) -> int:
        return self._engine.now

    @property
    def catalog(self):
        return self._engine.catalog

    def equity_now(self) -> float:
        return self._engine.equity_now()

    def drawdown_now(self) -> float:
        return self._engine.drawdown_now()

    def bars_for(self, tf: str):
        return self._engine.bars_for(tf)

    def forming_for(self, tf: str):
        return self._engine.forming_for(tf)

    # --- opening entries: through the gate ---
    def submit(self, side_sign: int, size: float, weight: float = 0.0, stop=None) -> None:
        size = self._gate_open(side_sign, size, "market", None)
        if size > 0.0:
            self._engine.submit(side_sign, size, weight=weight, stop=stop)

    def submit_limit(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        size = self._gate_open(side_sign, size, "limit", price)
        if size > 0.0:
            self._engine.submit_limit(side_sign, size, price, weight=weight)

    def submit_stop(self, side_sign: int, size: float, price: float, weight: float = 0.0) -> None:
        size = self._gate_open(side_sign, size, "stop", price)
        if size > 0.0:
            self._engine.submit_stop(side_sign, size, price, weight=weight)

    def submit_trailing(self, side_sign: int, size: float, trail: float, weight: float = 0.0) -> None:
        size = self._gate_open(side_sign, size, "market", None)
        if size > 0.0:
            self._engine.submit_trailing(side_sign, size, trail, weight=weight)

    # --- closes / targets / cancel: pass through (always manageable) ---
    def submit_close(self) -> None:
        self._engine.submit_close()

    def submit_market_close(self, side_sign: int, size: float) -> None:
        self._engine.submit_market_close(side_sign, size)

    def submit_limit_close(self, side_sign: int, size: float, price: float) -> None:
        self._engine.submit_limit_close(side_sign, size, price)

    def order_target(self, target: float) -> None:
        self._engine.order_target(target)

    def order_target_value(self, value: float) -> None:
        self._engine.order_target_value(value)

    def order_target_percent(self, pct: float) -> None:
        self._engine.order_target_percent(pct)

    def cancel_all(self) -> None:
        self._engine.cancel_all()

    # --- gate an opening entry; returns the (possibly rounded) size, or 0.0 if denied ---
    def _gate_open(self, side_sign: int, size: float, order_type: str, price: float | None) -> float:
        if self._gate is None:
            return size                                  # transparent pass-through
        pos = self._engine.position.size
        if not (pos == 0.0 or (pos > 0.0) == (side_sign > 0.0)):
            return size   # reducing / closing / flipping (incl. protective exits) — always passes
        is_stop = order_type == "stop"
        req = OrderRequest(
            client_order_id=f"{self._venue}-{next(self._seq)}", venue=self._venue,
            symbol=self._symbol, side=side_sign, qty=size, order_type=order_type,
            price=None if is_stop else price,
            trigger_price=price if is_stop else None,
        )
        mark = self._mark_price_fn() if self._mark_price_fn is not None else self._engine._price
        now = self._now_fn() if self._now_fn is not None else self._engine._now
        ctx = RiskContext(
            position_size=self._engine.position.size,
            mark_price=mark,
            now_ms=now,
        )
        verdict = self._gate.check(req, ctx)
        return verdict.request.qty if (verdict.ok and verdict.request is not None) else 0.0
