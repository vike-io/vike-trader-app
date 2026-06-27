"""Pluggable fill-price models, selected by available data (tiered fill engine).

- ``BarFillModel`` (no ticks): today's behavior — delegate to ``order_fill_price``.
- ``TickFillModel`` (L1 quote bars): market orders cross the real spread (buy@ask /
  sell@bid) when the bar carries bid/ask; everything else delegates.
The later L1+L2 ``L2BookFillModel`` tier plugs in here as a third implementation.
All tiers return a RAW price; the engine still applies ``adverse_fill_price`` slippage
and routes the fill through ``compute_fill`` (parity preserved).
"""

from .orders import Order, order_fill_price


class BarFillModel:
    """Bar-level fills (no tick data) — identical to the pre-tick engine."""

    def fill_price(self, order: Order, bar) -> float | None:
        return order_fill_price(order, bar)


class TickFillModel:
    """L1 spread-crossing for market orders; delegates limit/stop/close to bar fills."""

    def fill_price(self, order: Order, bar) -> float | None:
        if order.kind == "market" and bar.bid is not None and bar.ask is not None:
            return bar.ask if order.side > 0 else bar.bid
        return order_fill_price(order, bar)
