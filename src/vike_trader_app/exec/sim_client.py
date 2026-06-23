"""SimulatedExecutionClient — bridges BacktestEngine fills onto the Phase-0 event spine.

Registers the engine's optional ``on_fill`` hook and publishes one immutable ``FillEvent`` per fill
on the ``EventBus``, SYNCHRONOUSLY on the calling thread during ``step()``. Only LIVE clients cross a
thread boundary (stress-test #6); the sim publishes inline so the deterministic next-open fill the
parity tests pin is preserved. This is how the SAME ``Strategy`` run drives the event spine in
backtest/paper with zero new threading and zero behavior change (the engine hook is default-off; this
client opts it on).
"""

from __future__ import annotations

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent


class SimulatedExecutionClient:
    """Publishes a ``FillEvent`` per engine fill onto ``bus``. Construct it on an engine before running."""

    def __init__(self, engine, bus: EventBus, *, venue: str = "sim", symbol: str = "",
                 client_order_id: str = "sim") -> None:
        self.engine = engine
        self.bus = bus
        self.venue = venue
        self.symbol = symbol
        self._client_order_id = client_order_id
        self._n = 0
        engine._on_fill = self._on_engine_fill

    def _on_engine_fill(self, side_sign: int, size: float, price: float, fee: float,
                        ts: int, is_maker: bool) -> None:
        ev = FillEvent(
            trade_id=f"{self.venue}-{self._n}",
            client_order_id=self._client_order_id,
            venue=self.venue,
            symbol=self.symbol,
            side=side_sign,
            last_qty=size,
            last_px=price,
            commission=fee,
            liquidity_side="maker" if is_maker else "taker",
            ts=ts,
        )
        self._n += 1
        self.bus.publish(ev)
