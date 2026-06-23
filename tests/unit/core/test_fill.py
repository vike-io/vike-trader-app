"""compute_fill: the one cost-basis primitive (open/add/reduce/flip/close), shared by all engines."""

from vike_trader_app.core.fill import compute_fill


def test_open_from_flat():
    o = compute_fill(0.0, 0.0, +1, 2.0, 100.0)
    assert o.kind == "open" and o.new_size == 2.0 and o.new_avg_px == 100.0
    assert o.closing_qty == 0.0 and o.realized_pnl == 0.0


def test_add_same_direction_weighted_average():
    o = compute_fill(2.0, 100.0, +1, 2.0, 120.0)  # avg of 2@100 + 2@120 = 110
    assert o.kind == "add" and o.new_size == 4.0 and o.new_avg_px == 110.0
    assert o.closing_qty == 0.0


def test_partial_reduce_keeps_cost_basis():
    o = compute_fill(4.0, 110.0, -1, 1.0, 130.0)  # close 1 of a +4 long
    assert o.kind == "reduce" and o.new_size == 3.0 and o.new_avg_px == 110.0
    assert o.closing_qty == 1.0 and o.entry_avg_px == 110.0
    assert o.realized_pnl == (130.0 - 110.0) * 1.0  # (price-avg)*closing*mult
    assert o.portion == 0.25 and o.leftover == 0.0


def test_full_close():
    o = compute_fill(2.0, 100.0, -1, 2.0, 130.0)
    assert o.kind == "close" and o.new_size == 0.0 and o.new_avg_px == 0.0
    assert o.closing_qty == 2.0 and o.realized_pnl == (130.0 - 100.0) * 2.0


def test_close_and_flip():
    o = compute_fill(1.0, 100.0, -1, 3.0, 120.0)  # close 1, open -2 @ 120
    assert o.kind == "flip" and o.new_size == -2.0 and o.new_avg_px == 120.0
    assert o.closing_qty == 1.0 and o.leftover == 2.0
    assert o.realized_pnl == (120.0 - 100.0) * 1.0


def test_short_realized_pnl_is_signed():
    o = compute_fill(-1.0, 130.0, +1, 1.0, 110.0)  # cover a short
    assert o.kind == "close" and o.realized_pnl == (110.0 - 130.0) * (-1.0)  # = +20


def test_multiplier_scales_realized():
    o = compute_fill(1.0, 100.0, -1, 1.0, 110.0, multiplier=10.0)
    assert o.realized_pnl == (110.0 - 100.0) * 1.0 * 10.0
