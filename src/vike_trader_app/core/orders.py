"""Resting-order record + the pure fill-trigger, shared by the single-symbol and portfolio engines.

One definition of WHEN a limit/stop/trailing order fills and at WHAT price, so both engines stay in
parity. ``order_fill_price`` is pure w.r.t. the bar but ratchets a trailing order's ``extreme`` in
place (testing the PRIOR extreme's trigger first, so a new-high bar can't stop out on its own low).
"""

from dataclasses import dataclass


@dataclass
class Order:
    """A pending order. ``kind`` in {market, limit, stop, trailing}."""

    kind: str
    side: int                     # +1 buy / -1 sell
    size: float
    price: float | None = None    # limit/stop trigger
    trail: float | None = None    # trailing distance (absolute)
    extreme: float | None = None  # running best price since submission (trailing only)
    weight: float = 0.0           # Transaction.Weight: cross-symbol fill priority (higher fills first)


def order_fill_price(o: "Order", bar):
    """Fill price for ``o`` against ``bar``, or None if it doesn't trigger.

    Trailing stops check the prior extreme's trigger first, then ratchet the extreme with this bar.
    """
    if o.kind == "market":
        return bar.open
    if o.kind == "limit":  # buy on a dip to price; sell on a rally to price
        if o.side > 0:
            return o.price if bar.low <= o.price else None
        return o.price if bar.high >= o.price else None
    if o.kind == "stop":  # buy on breakout up; sell on breakdown
        if o.side > 0:
            return o.price if bar.high >= o.price else None
        return o.price if bar.low <= o.price else None
    # trailing: side<0 protects a long (sell-stop trailing the high);
    #           side>0 protects a short (buy-stop trailing the low).
    if o.side < 0:
        trigger = o.extreme - o.trail
        if bar.low <= trigger:
            return trigger
        o.extreme = max(o.extreme, bar.high)
        return None
    trigger = o.extreme + o.trail
    if bar.high >= trigger:
        return trigger
    o.extreme = min(o.extreme, bar.low)
    return None
