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
    """L1 spread-crossing. For a SINGLE quote tick (bid/ask present AND high == low) every order
    kind crosses the real spread — buys reference the ask, sells the bid — for both the trigger
    test and the fill price. For a consolidated bar (high != low) or an event without quotes,
    delegate to ``order_fill_price`` unchanged (Slice-1 behavior). Returns a RAW price; the engine
    still applies ``adverse_fill_price`` slippage and routes the fill through ``compute_fill``."""

    def fill_price(self, order: Order, bar) -> float | None:
        if bar.bid is None or bar.ask is None or bar.high != bar.low:
            return order_fill_price(order, bar)   # consolidated bar / no quote -> unchanged
        buy = order.side > 0
        quote = bar.ask if buy else bar.bid       # the side this order transacts on
        kind = order.kind
        if kind in ("market", "market_close"):
            return quote
        if kind in ("limit", "limit_close"):
            # buy fills when the ask has reached/under the limit; sell when the bid is at/over it
            if buy:
                return quote if bar.ask <= order.price else None
            return quote if bar.bid >= order.price else None
        if kind == "stop":
            # buy stop triggers when the ask reaches/over the stop; sell stop when the bid is at/under
            if buy:
                return quote if bar.ask >= order.price else None
            return quote if bar.bid <= order.price else None
        # trailing: side<0 protects a long (sell-stop trailing the bid); side>0 protects a short
        if order.side < 0:
            trigger = order.extreme - order.trail
            if bar.bid <= trigger:
                return bar.bid
            order.extreme = max(order.extreme, bar.bid)
            return None
        trigger = order.extreme + order.trail
        if bar.ask >= trigger:
            return bar.ask
        order.extreme = min(order.extreme, bar.ask)
        return None
