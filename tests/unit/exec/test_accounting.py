"""Account read-model folds the FillEvent stream into positions + realized PnL (engine-mirroring)."""

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import FillEvent


def _fill(side, qty, px, tid="t", venue="sim", symbol="X"):
    # side: ±1 for direction, qty: quantity
    return FillEvent(trade_id=tid, client_order_id="c", venue=venue, symbol=symbol,
                     side=side, last_qty=qty, last_px=px)


def _key(venue="sim", symbol="X"):
    return (venue, symbol, "BOTH")


def test_open_then_close_realizes_price_pnl():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 110.0, "t0"))
    acc.apply_fill(_fill(-1, 1.0, 130.0, "t1"))
    assert acc.realized_pnl == 20.0
    assert acc.trades == [20.0]
    assert acc.positions[_key()]["size"] == 0.0


def test_add_same_direction_averages_cost():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, "t0"))
    acc.apply_fill(_fill(+1, 1.0, 120.0, "t1"))  # avg = 110
    assert acc.positions[_key()]["size"] == 2.0
    assert acc.positions[_key()]["avg_px"] == 110.0
    acc.apply_fill(_fill(-1, 2.0, 130.0, "t2"))  # pnl = (130-110)*2 = 40
    assert acc.trades == [40.0]
    assert acc.positions[_key()]["size"] == 0.0


def test_partial_reduce_keeps_remainder_at_cost():
    acc = Account()
    acc.apply_fill(_fill(+1, 2.0, 100.0, "t0"))
    acc.apply_fill(_fill(-1, 1.0, 120.0, "t1"))  # close 1 -> pnl (120-100)*1 = 20
    assert acc.trades == [20.0]
    assert acc.positions[_key()]["size"] == 1.0
    assert acc.positions[_key()]["avg_px"] == 100.0  # remainder unchanged


def test_close_and_flip_opens_opposite_at_fill_price():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, "t0"))
    acc.apply_fill(_fill(-1, 3.0, 120.0, "t1"))  # close 1 (pnl 20), flip to -2 @ 120
    assert acc.trades == [20.0]
    assert acc.positions[_key()]["size"] == -2.0
    assert acc.positions[_key()]["avg_px"] == 120.0


def test_short_pnl_is_signed():
    acc = Account()
    acc.apply_fill(_fill(-1, 1.0, 130.0, "t0"))  # open short
    acc.apply_fill(_fill(+1, 1.0, 110.0, "t1"))  # cover -> pnl (110-130)*(-1) = 20
    assert acc.realized_pnl == 20.0
    assert acc.trades == [20.0]


def test_multiplier_scales_pnl():
    acc = Account(multiplier=10.0)
    acc.apply_fill(_fill(+1, 1.0, 100.0, "t0"))
    acc.apply_fill(_fill(-1, 1.0, 110.0, "t1"))
    assert acc.trades == [100.0]  # (110-100)*1*10


def test_flip_then_close_realizes_each_leg():
    acc = Account()
    acc.apply_fill(_fill(+1, 2.0, 100.0, "t0"))   # open long 2 @ 100
    acc.apply_fill(_fill(-1, 3.0, 120.0, "t1"))   # close 2 (pnl (120-100)*2=40), flip to -1 @ 120
    acc.apply_fill(_fill(+1, 1.0, 110.0, "t2"))   # close the -1 short (pnl (110-120)*-1=10) -> flat
    assert acc.trades == [40.0, 10.0]
    assert acc.realized_pnl == 50.0
    assert acc.positions[_key()]["size"] == 0.0
