"""Pure build_reconcile_snapshot: scripted Deribit private-getter result lists -> ReconcileSnapshot.

Covers the 6d correctness traps: UNSIGNED size + separate direction (sign applied here), filter to the
armed instrument, flat one-zero-row branch, label->coid, order_state filtering, empty-label skip,
PARTIALLY_FILLED seed, and the 'market_price' string-price guard.
"""
from vike_trader_app.exec.crypto_client import ReconcileSnapshot
from vike_trader_app.exec.deribit.reconcile import build_reconcile_snapshot
from vike_trader_app.exec.order import ManagedOrder, OrderStatus

_SYM = "BTC-27JUN26-60000-C"


def test_long_position_signed_positive():
    pos = [{"instrument_name": _SYM, "direction": "buy", "size": 2.5,
            "average_price": 0.05, "mark_price": 0.052, "kind": "option"}]
    snap = build_reconcile_snapshot(pos, [], _SYM)
    assert isinstance(snap, ReconcileSnapshot)
    assert snap.positions == ((_SYM, 2.5),)
    assert snap.position_avg_px == ((_SYM, 0.05),)
    assert snap.position_mark_px == ((_SYM, 0.052),)
    assert snap.position_sides == ()          # one-way option -> BOTH


def test_short_position_signed_negative():
    """size is UNSIGNED; direction=='sell' makes it negative (the #1 trap)."""
    pos = [{"instrument_name": _SYM, "direction": "sell", "size": 3.0,
            "average_price": 0.04, "mark_price": 0.038}]
    snap = build_reconcile_snapshot(pos, [], _SYM)
    assert snap.positions == ((_SYM, -3.0),)
    assert snap.position_avg_px == ((_SYM, 0.04),)


def test_filters_to_armed_instrument():
    """get_positions returns the whole currency; only the armed instrument is kept (the #2 trap)."""
    pos = [
        {"instrument_name": "BTC-27JUN26-70000-C", "direction": "buy", "size": 9.0,
         "average_price": 0.01, "mark_price": 0.01},
        {"instrument_name": _SYM, "direction": "buy", "size": 1.0,
         "average_price": 0.05, "mark_price": 0.05},
    ]
    snap = build_reconcile_snapshot(pos, [], _SYM)
    assert snap.positions == ((_SYM, 1.0),)   # the other strike is dropped


def test_flat_when_instrument_absent():
    snap = build_reconcile_snapshot([], [], _SYM)
    assert snap.positions == ((_SYM, 0.0),)
    assert snap.position_avg_px == ((_SYM, 0.0),)
    assert snap.position_mark_px == ((_SYM, 0.0),)


def test_flat_when_direction_zero():
    pos = [{"instrument_name": _SYM, "direction": "zero", "size": 0,
            "average_price": 0.05, "mark_price": 0.05}]
    snap = build_reconcile_snapshot(pos, [], _SYM)
    assert snap.positions == ((_SYM, 0.0),)


def test_open_order_maps_to_accepted_managed_order():
    orders = [{"order_id": "146062", "instrument_name": _SYM, "direction": "buy",
               "amount": 10.0, "filled_amount": 0.0, "average_price": 0.0,
               "price": 0.0028, "order_state": "open", "order_type": "limit",
               "label": "vike-7"}]
    snap = build_reconcile_snapshot([], orders, _SYM)
    assert len(snap.open_orders) == 1
    mo = snap.open_orders[0]
    assert isinstance(mo, ManagedOrder)
    assert mo.status is OrderStatus.ACCEPTED
    assert mo.client_order_id == "vike-7"        # label IS the coid
    assert mo.venue_order_id == "146062"         # str-cast
    assert mo.request.side == 1
    assert mo.request.qty == 10.0
    assert mo.request.price == 0.0028
    assert mo.filled_qty == 0.0


def test_partially_filled_open_order_seeds_partial_status():
    """filled_amount>0 -> PARTIALLY_FILLED so a later live WS fill transitions legally."""
    orders = [{"order_id": "200", "instrument_name": _SYM, "direction": "sell",
               "amount": 5.0, "filled_amount": 2.0, "average_price": 0.06,
               "price": 0.061, "order_state": "open", "order_type": "limit",
               "label": "vike-9"}]
    mo = build_reconcile_snapshot([], orders, _SYM).open_orders[0]
    assert mo.status is OrderStatus.PARTIALLY_FILLED
    assert mo.filled_qty == 2.0
    assert mo.avg_fill_px == 0.06
    assert mo.request.side == -1


def test_empty_label_order_skipped():
    """A web-UI / external order (label=='') is NOT managed (would collide on '' coid)."""
    orders = [{"order_id": "300", "instrument_name": _SYM, "direction": "buy",
               "amount": 1.0, "filled_amount": 0.0, "average_price": 0.0,
               "price": 0.05, "order_state": "open", "order_type": "limit", "label": ""}]
    snap = build_reconcile_snapshot([], orders, _SYM)
    assert snap.open_orders == ()


def test_terminal_state_orders_dropped():
    orders = [
        {"order_id": "1", "instrument_name": _SYM, "direction": "buy", "amount": 1.0,
         "filled_amount": 1.0, "average_price": 0.05, "price": 0.05,
         "order_state": "filled", "order_type": "limit", "label": "vike-a"},
        {"order_id": "2", "instrument_name": _SYM, "direction": "buy", "amount": 1.0,
         "filled_amount": 0.0, "average_price": 0.0, "price": 0.05,
         "order_state": "cancelled", "order_type": "limit", "label": "vike-b"},
    ]
    snap = build_reconcile_snapshot([], orders, _SYM)
    assert snap.open_orders == ()


def test_market_price_string_price_does_not_crash():
    """A trigger order with price=='market_price' must not crash float(); price -> None."""
    orders = [{"order_id": "9", "instrument_name": _SYM, "direction": "buy",
               "amount": 1.0, "filled_amount": 0.0, "average_price": 0.0,
               "price": "market_price", "order_state": "open", "order_type": "market",
               "label": "vike-m"}]
    mo = build_reconcile_snapshot([], orders, _SYM).open_orders[0]
    assert mo.request.price is None


def test_positions_and_orders_together():
    pos = [{"instrument_name": _SYM, "direction": "buy", "size": 4.0,
            "average_price": 0.05, "mark_price": 0.051}]
    orders = [{"order_id": "146062", "instrument_name": _SYM, "direction": "sell",
               "amount": 2.0, "filled_amount": 0.0, "average_price": 0.0,
               "price": 0.07, "order_state": "open", "order_type": "limit",
               "label": "vike-1"}]
    snap = build_reconcile_snapshot(pos, orders, _SYM)
    assert snap.positions == ((_SYM, 4.0),)
    assert len(snap.open_orders) == 1
    assert snap.open_orders[0].request.side == -1
