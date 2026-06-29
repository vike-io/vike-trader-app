"""Unit tests for the shared intrabar fill-resolution component (fill_resolution.py).

Covers:
- Adverse-first ordering (stop/trailing sorted before limit)
- SL+TP bracket cap: stop fills first, limit capped to remaining (zero), both_hit=1
- No bracket cap when only one reducer
- No bracket cap when all reducers are the same kind
- both_hit=0 when bracket does not apply
- Flat position (closing_side=0): no cap, no both_hit
"""

from vike_trader_app.core.fill_resolution import resolve_intrabar_fills
from vike_trader_app.core.orders import Order


def _order(kind: str, side: int, size: float = 1.0) -> Order:
    price = 100.0 if kind in ("limit", "stop") else None
    return Order(kind=kind, side=side, size=size, price=price)


# ---------------------------------------------------------------------------
# Adverse-first ordering
# ---------------------------------------------------------------------------

def test_adverse_first_ordering_stop_before_limit():
    """Stop orders must sort before limit orders regardless of input order."""
    lim = _order("limit", -1, size=1.0)
    stop = _order("stop", -1, size=1.0)
    triggered = [(lim, 101.0), (stop, 99.0)]  # limit first in input

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=2.0)

    # After sorting: stop first, then limit
    assert resolved[0][0].kind == "stop"
    assert resolved[1][0].kind == "limit"


def test_trailing_also_sorted_adverse_first():
    lim = _order("limit", -1, size=1.0)
    trail = Order(kind="trailing", side=-1, size=1.0, trail=1.0, extreme=102.0)
    triggered = [(lim, 101.0), (trail, 99.0)]

    resolved, _ = resolve_intrabar_fills(triggered, position_size=2.0)
    assert resolved[0][0].kind == "trailing"
    assert resolved[1][0].kind == "limit"


# ---------------------------------------------------------------------------
# Bracket cap — long position (pos>0, reducers side=-1)
# ---------------------------------------------------------------------------

def test_bracket_cap_long_stop_fills_limit_capped_to_zero():
    """Long position 1 unit: stop fills 1, limit capped to 0, both_hit=1."""
    stop = _order("stop", -1, size=1.0)
    lim = _order("limit", -1, size=1.0)
    triggered = [(lim, 102.0), (stop, 99.0)]  # limit first (adversarial input order)

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=1.0)

    assert both_hit == 1
    # After adverse-first sort: stop is [0], limit is [1]
    stop_order = next(o for o, _ in resolved if o.kind == "stop")
    lim_order = next(o for o, _ in resolved if o.kind == "limit")
    assert stop_order.size == 1.0   # stop takes the full position
    assert lim_order.size == 0.0   # limit capped to 0


def test_bracket_cap_long_partial_remaining():
    """Long position 3 units: stop size=2 fills, limit capped to remaining 1."""
    stop = _order("stop", -1, size=2.0)
    lim = _order("limit", -1, size=2.0)
    triggered = [(stop, 99.0), (lim, 102.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=3.0)

    assert both_hit == 1
    stop_order = next(o for o, _ in resolved if o.kind == "stop")
    lim_order = next(o for o, _ in resolved if o.kind == "limit")
    assert stop_order.size == 2.0
    assert lim_order.size == 1.0   # only 1 unit remaining after stop


# ---------------------------------------------------------------------------
# Bracket cap — short position (pos<0, reducers side=+1)
# ---------------------------------------------------------------------------

def test_bracket_cap_short_position():
    """Short position -1 unit: buy-stop fills first, buy-limit capped to 0."""
    stop = _order("stop", +1, size=1.0)
    lim = _order("limit", +1, size=1.0)
    triggered = [(lim, 98.0), (stop, 101.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=-1.0)

    assert both_hit == 1
    stop_order = next(o for o, _ in resolved if o.kind == "stop")
    lim_order = next(o for o, _ in resolved if o.kind == "limit")
    assert stop_order.size == 1.0
    assert lim_order.size == 0.0


# ---------------------------------------------------------------------------
# No bracket cap cases
# ---------------------------------------------------------------------------

def test_no_cap_when_single_reducer():
    """Only one reducing order: cap logic doesn't apply, both_hit=0."""
    stop = _order("stop", -1, size=1.0)
    triggered = [(stop, 99.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=1.0)
    assert both_hit == 0
    assert resolved[0][0].size == 1.0


def test_no_cap_when_all_stops_no_limit():
    """Two stops but no limit: no bracket cap, both_hit=0."""
    stop1 = _order("stop", -1, size=0.5)
    stop2 = _order("stop", -1, size=0.5)
    triggered = [(stop1, 99.0), (stop2, 98.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=1.0)
    assert both_hit == 0
    assert stop1.size == 0.5
    assert stop2.size == 0.5


def test_no_cap_when_all_limits_no_stop():
    """Two limits but no stop: no bracket cap, both_hit=0."""
    lim1 = _order("limit", -1, size=0.5)
    lim2 = _order("limit", -1, size=0.5)
    triggered = [(lim1, 102.0), (lim2, 103.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=1.0)
    assert both_hit == 0
    assert lim1.size == 0.5
    assert lim2.size == 0.5


def test_flat_position_no_cap():
    """Flat position: closing_side=0, no cap, both_hit=0."""
    stop = _order("stop", -1, size=1.0)
    lim = _order("limit", +1, size=1.0)
    triggered = [(lim, 102.0), (stop, 99.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=0.0)
    assert both_hit == 0


def test_adding_orders_not_capped():
    """A stop and limit that ADD to position (same side as position): no cap."""
    # Long position, both orders also buy (+1): they ADD, not reduce
    stop = _order("stop", +1, size=1.0)   # buy-stop entry
    lim = _order("limit", +1, size=1.0)   # buy-limit entry
    triggered = [(lim, 98.0), (stop, 101.0)]

    resolved, both_hit = resolve_intrabar_fills(triggered, position_size=1.0)
    # closing_side for long is -1; these are +1 orders → not reducers → no cap
    assert both_hit == 0
    assert stop.size == 1.0
    assert lim.size == 1.0


# ---------------------------------------------------------------------------
# Return value shape
# ---------------------------------------------------------------------------

def test_returns_same_list_reference_mutated():
    """resolve_intrabar_fills returns the same order objects (mutated in place)."""
    stop = _order("stop", -1, size=1.0)
    lim = _order("limit", -1, size=1.0)
    triggered = [(stop, 99.0), (lim, 102.0)]

    resolved, _ = resolve_intrabar_fills(triggered, position_size=1.0)
    # The order objects are the same Python objects (identity)
    resolved_orders = [o for o, _ in resolved]
    assert stop in resolved_orders
    assert lim in resolved_orders
