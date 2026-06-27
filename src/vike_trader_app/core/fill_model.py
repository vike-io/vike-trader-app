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
    """L1 spread-crossing fill model.

    - MARKET orders always cross the real spread (buy@ask / sell@bid) whenever quotes are
      present, regardless of bar shape (single tick or consolidated multi-tick bar).
      This preserves the Slice-1 guarantee.
    - Quote-side resting-order logic (limit/stop/trailing/market_close) applies only to a
      SINGLE quote tick (high == low).  A consolidated bar (high != low) delegates those
      order kinds to ``order_fill_price`` unchanged (Slice-1 behavior preserved).
    - Returns a RAW price; the engine still applies ``adverse_fill_price`` slippage and
      routes the fill through ``compute_fill``.
    """

    def fill_price(self, order: Order, bar) -> float | None:
        has_quote = bar.bid is not None and bar.ask is not None
        buy = order.side > 0
        kind = order.kind
        # Slice-1 preserved: a MARKET order crosses the real spread whenever quotes are present,
        # for ANY bar shape (consolidated multi-tick bar or single tick).
        if kind == "market" and has_quote:
            return bar.ask if buy else bar.bid
        # Slice-2: quote-side triggering for resting orders + market_close, ONLY for a SINGLE
        # quote tick (high == low). A consolidated bar (high != low) delegates (Slice-1 unchanged).
        if has_quote and bar.high == bar.low:
            quote = bar.ask if buy else bar.bid
            if kind == "market_close":
                return quote
            if kind in ("limit", "limit_close"):
                if buy:
                    return quote if bar.ask <= order.price else None
                return quote if bar.bid >= order.price else None
            if kind == "stop":
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
        return order_fill_price(order, bar)
