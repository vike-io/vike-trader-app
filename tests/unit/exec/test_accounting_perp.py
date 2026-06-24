"""Perp account extensions: balance, mark price, unrealized PnL — engine-shape math, offline."""

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import FillEvent


def _fill(side, qty, px, tid="t", venue="binance", symbol="BTCUSDT", pside="BOTH"):
    return FillEvent(trade_id=tid, client_order_id="c", venue=venue, symbol=symbol,
                     side=side, last_qty=qty, last_px=px, position_side=pside)


def test_balance_starts_at_zero():
    assert Account().balance == 0.0


def test_set_mark_then_unrealized_long():
    acc = Account()
    acc.apply_fill(_fill(+1, 2.0, 100.0))        # long 2 @ 100
    acc.set_mark("binance", "BTCUSDT", 110.0)
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 20.0   # (110-100)*2


def test_unrealized_short_is_signed():
    acc = Account()
    acc.apply_fill(_fill(-1, 1.0, 130.0))        # short 1 @ 130
    acc.set_mark("binance", "BTCUSDT", 110.0)
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 20.0   # (110-130)*(-1)


def test_unrealized_zero_when_flat_or_unmarked():
    acc = Account()
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 0.0    # no position, no mark
    acc.apply_fill(_fill(+1, 1.0, 100.0))
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 0.0    # position but no mark set


def test_unrealized_scales_with_multiplier():
    acc = Account(multiplier=10.0)
    acc.apply_fill(_fill(+1, 1.0, 100.0))
    acc.set_mark("binance", "BTCUSDT", 110.0)
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 100.0  # (110-100)*1*10


def test_apply_fill_keys_off_position_side():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, "l0", pside="LONG"))
    acc.apply_fill(_fill(-1, 1.0, 200.0, "s0", pside="SHORT"))
    assert acc.positions[("binance", "BTCUSDT", "LONG")]["size"] == 1.0
    assert acc.positions[("binance", "BTCUSDT", "SHORT")]["size"] == -1.0
    assert ("binance", "BTCUSDT", "BOTH") not in acc.positions


def test_spot_fill_still_keys_both():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, "t0"))   # default pside="BOTH"
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 1.0
