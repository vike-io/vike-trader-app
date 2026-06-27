"""Pure Deribit private getter results -> a populated ReconcileSnapshot (no socket; scripted-frame
unit-tested). 6d's connect() calls private/get_positions + private/get_open_orders_by_instrument over
the authed transport and hands the two result LISTs here.

LIKE Binance positionAmt: Deribit get_positions `size` is ALREADY SIGNED (negative=short,
positive=long); `direction` ('buy'/'sell'/'zero') is a redundant companion we read ONLY for the flat
guard — NOT for the sign (re-signing it would invert every short). size/avg/mark are COIN units for
options (no ct_val rescale).

Options are ONE-WAY: a single row per instrument, position_side='BOTH' -> position_sides left ()
(byte-equivalent default). The result is filtered to the armed `symbol` (the hub is per-instrument and
apply_snapshot asserts sym == hub.symbol, live_oms.py:102).

Open orders: `label` IS the vike client_order_id (mapper.py:31). Orders with an EMPTY label are
externally placed (web UI / another client) and are SKIPPED on reconcile — we only manage vike-labelled
orders (a colliding '' coid would be un-cancellable in the registry). Only order_state=='open' is
seeded (terminal states dropped; conditional untriggered/triggered, whose price may be the string
'market_price', deferred to a later slice). filled_amount>0 seeds PARTIALLY_FILLED so a later live WS
fill transitions legally (order.py:67).
"""
from __future__ import annotations

from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.order import ManagedOrder, OrderStatus

_VENUE = "deribit"


def _numeric_or_none(value) -> float | None:
    """Deribit `price` is numeric for limit orders but the literal string 'market_price' for open
    trigger market orders — float() would crash. Return None for any non-numeric value."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_positions(positions_result, symbol: str):
    """Filter to `symbol`, read the ALREADY-SIGNED size, drop zero/flat rows. Returns the live leg
    tuple (signed_size, avg_px, mark_px) or None when flat."""
    for row in positions_result or ():
        if str(row.get("instrument_name", "")) != symbol:
            continue
        direction = str(row.get("direction", ""))
        # Deribit `size` is already signed (negative=short, positive=long); `direction` is redundant.
        # Read size DIRECTLY — re-signing by direction inverts shorts. `direction`=='zero' guards flat.
        size = float(row.get("size", 0) or 0)
        if direction == "zero" or size == 0.0:
            continue
        return (size,
                float(row.get("average_price", 0) or 0),
                float(row.get("mark_price", 0) or 0))
    return None


def _build_orders(orders_result, symbol: str):
    """order_state=='open' rows with a non-empty label -> ManagedOrders (label=coid, signed side,
    PARTIALLY_FILLED when filled_amount>0)."""
    out: list[ManagedOrder] = []
    for row in orders_result or ():
        if str(row.get("order_state", "")) != "open":
            continue
        label = str(row.get("label", ""))
        if not label:
            continue  # externally-placed order (no vike coid) — not ours to manage
        side = 1 if row.get("direction") == "buy" else -1
        filled = float(row.get("filled_amount", 0) or 0)
        status = OrderStatus.PARTIALLY_FILLED if filled > 0 else OrderStatus.ACCEPTED
        req = OrderRequest(
            client_order_id=label, venue=_VENUE, symbol=symbol, side=side,
            qty=float(row.get("amount", 0) or 0),
            order_type=str(row.get("order_type", "limit")),
            price=_numeric_or_none(row.get("price")))
        out.append(ManagedOrder(
            request=req, status=status, venue_order_id=str(row.get("order_id", "")),
            filled_qty=filled, avg_fill_px=float(row.get("average_price", 0) or 0)))
    return out


def build_reconcile_snapshot(positions_result, orders_result, symbol: str) -> ReconcileSnapshot:
    """Assemble the populated ReconcileSnapshot apply_snapshot consumes. Flat -> one zero BOTH row
    (mirrors the perp flat branch); options are one-way so position_sides stays ()."""
    orders = tuple(_build_orders(orders_result, symbol))
    leg = _build_positions(positions_result, symbol)
    if leg is None:
        return ReconcileSnapshot(
            positions=((symbol, 0.0),), open_orders=orders,
            position_avg_px=((symbol, 0.0),), position_mark_px=((symbol, 0.0),))
    signed, avg, mark = leg
    return ReconcileSnapshot(
        positions=((symbol, signed),), open_orders=orders,
        position_avg_px=((symbol, avg),), position_mark_px=((symbol, mark),))
