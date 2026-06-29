"""Resting-order record + the pure fill-trigger, shared by the single-symbol and portfolio engines.

One definition of WHEN a limit/stop/trailing order fills and at WHAT price, so both engines stay in
parity. ``order_fill_price`` is pure w.r.t. the bar but ratchets a trailing order's ``extreme`` in
place (testing the PRIOR extreme's trigger first, so a new-high bar can't stop out on its own low).

``order_request_to_resting`` is the Task B2 adapter — it lives in ``core.order_intent`` (where
``OrderRequest`` is defined) and is re-exported here for discoverability.
"""

from dataclasses import dataclass

# Re-export the adapter so callers can import from either module.
from vike_trader_app.core.order_intent import order_request_to_resting as order_request_to_resting  # noqa: F401


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
    stop: float | None = None     # protective stop to arm when this entry fills (risk sizing; portfolio only)


def order_fill_price(o: "Order", bar):
    """Fill price for ``o`` against ``bar``, or None if it doesn't trigger.

    Gap-open normalization: a bar that OPENS past the trigger never traded at the trigger price, so
    the realistic fill is the (gapped) open — ADVERSE for stops (you're filled worse than the stop)
    and FAVOURABLE for limits (price improvement: you're filled better than the limit). Within a
    non-gapping bar this collapses to the trigger price, so it's a no-op for ordinary fills.

    Trailing stops check the prior extreme's trigger first, then ratchet the extreme with this bar.
    """
    if o.kind == "market":
        return bar.open
    if o.kind == "market_close":
        return bar.close
    if o.kind == "limit_close":  # fills at the close only if the close is at-or-better than the limit
        if o.side > 0:
            return bar.close if bar.close <= o.price else None
        return bar.close if bar.close >= o.price else None
    if o.kind == "limit":  # buy on a dip / sell on a rally — a gap through the open improves the fill
        if o.side > 0:
            return min(o.price, bar.open) if bar.low <= o.price else None
        return max(o.price, bar.open) if bar.high >= o.price else None
    if o.kind == "stop":  # breakout up / breakdown — a gap through the open worsens the fill
        if o.side > 0:
            return max(o.price, bar.open) if bar.high >= o.price else None
        return min(o.price, bar.open) if bar.low <= o.price else None
    # trailing: side<0 protects a long (sell-stop trailing the high);
    #           side>0 protects a short (buy-stop trailing the low).
    if o.side < 0:
        trigger = o.extreme - o.trail
        if bar.low <= trigger:
            return min(trigger, bar.open)          # gap-down open fills below the trailing stop
        o.extreme = max(o.extreme, bar.high)
        return None
    trigger = o.extreme + o.trail
    if bar.high >= trigger:
        return max(trigger, bar.open)              # gap-up open fills above the trailing stop
    o.extreme = min(o.extreme, bar.low)
    return None


def order_fill_price_granular(o: "Order", sub_bars):
    """Resolve ``o`` against ordered finer ``sub_bars``.

    Returns ``(fill_price, sub_index)`` of the FIRST sub-bar that triggers it, or ``None``.
    ``market`` / ``market_close`` fill on the first sub-bar (open / close). ``limit`` / ``stop`` /
    ``trailing`` walk ``sub_bars`` in chronological order, returning the first trigger
    (``order_fill_price`` ratchets a trailing ``extreme`` in place as you go, exactly as it would over
    the equivalent coarse bar). Pure except for that trailing ratchet (same contract as
    ``order_fill_price``).
    """
    if not sub_bars:
        return None
    if o.kind in ("market", "market_close"):
        # Market(-close) fills on the first sub-bar; order_fill_price returns open/close there.
        return (order_fill_price(o, sub_bars[0]), 0)
    for i, sub in enumerate(sub_bars):
        fp = order_fill_price(o, sub)  # ratchets a trailing extreme in place per sub-bar
        if fp is not None:
            return (fp, i)
    return None
