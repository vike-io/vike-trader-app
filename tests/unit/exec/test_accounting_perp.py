"""Perp account extensions: balance, mark price, unrealized PnL — engine-shape math, offline."""

from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import FillEvent


def _fill(side, qty, px, tid="t", venue="binance", symbol="BTCUSDT", pside="BOTH"):
    return FillEvent(trade_id=tid, client_order_id="c", venue=venue, symbol=symbol,
                     side=side, last_qty=qty, last_px=px, position_side=pside)


def test_balance_starts_at_zero():
    assert Account().balance == 0.0


def test_set_mark_then_unrealized_long():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 2.0, 100.0))        # long 2 @ 100
    acc.set_mark("binance", "BTCUSDT", 110.0)
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 20.0   # (110-100)*2


def test_unrealized_short_is_signed():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(-1, 1.0, 130.0))        # short 1 @ 130
    acc.set_mark("binance", "BTCUSDT", 110.0)
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 20.0   # (110-130)*(-1)


def test_unrealized_zero_when_flat_or_unmarked():
    acc = Account(venue="binance")
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 0.0    # no position, no mark
    acc.apply_fill(_fill(+1, 1.0, 100.0))
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 0.0    # position but no mark set


def test_unrealized_scales_with_multiplier():
    acc = Account(multiplier=10.0, venue="binance")
    acc.apply_fill(_fill(+1, 1.0, 100.0))
    acc.set_mark("binance", "BTCUSDT", 110.0)
    assert acc.unrealized_pnl("binance", "BTCUSDT") == 100.0  # (110-100)*1*10


def test_apply_fill_keys_off_position_side():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 1.0, 100.0, "l0", pside="LONG"))
    acc.apply_fill(_fill(-1, 1.0, 200.0, "s0", pside="SHORT"))
    assert acc.positions[("binance", "BTCUSDT", "LONG")]["size"] == 1.0
    assert acc.positions[("binance", "BTCUSDT", "SHORT")]["size"] == -1.0
    assert ("binance", "BTCUSDT", "BOTH") not in acc.positions


def test_spot_fill_still_keys_both():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 1.0, 100.0, "t0"))   # default pside="BOTH"
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 1.0


from vike_trader_app.exec.events import FundingEvent


def test_apply_funding_credits_and_debits_balance():
    acc = Account()
    acc.apply_funding(FundingEvent(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                                   funding_rate=0.0001, amount=-1.50))
    acc.apply_funding(FundingEvent(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                                   funding_rate=-0.0002, amount=2.25))
    assert acc.balance == 0.75
    assert acc.funding_paid == 0.75


from vike_trader_app.exec.events import PositionLiquidated


def test_apply_liquidation_long_realizes_loss_and_flattens():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 2.0, 100.0))          # long 2 @ 100
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=2.0, liq_price=60.0, fee=0.5))
    # realized (60-100)*2 = -80 ; balance -fee
    assert acc.realized_pnl == -80.0
    assert acc.trades[-1] == -80.0
    assert acc.balance == -0.5
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0


def test_apply_liquidation_short_realizes_and_flattens():
    acc = Account(venue="binance")
    acc.apply_fill(_fill(-1, 1.0, 100.0))          # short 1 @ 100
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=1.0, liq_price=150.0, fee=0.0))
    assert acc.realized_pnl == -50.0               # (150-100)*(-1)
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0


def test_apply_liquidation_noop_when_flat():
    acc = Account()
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=1.0, liq_price=60.0, fee=0.5))
    assert acc.realized_pnl == 0.0
    assert acc.trades == []
    assert acc.balance == 0.0      # TRUE no-op: no fee when there is nothing to liquidate


def test_apply_liquidation_partial_closes_only_ev_qty():
    """A partial liquidation closes only ev.qty and leaves the residual at the same cost basis."""
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 2.0, 100.0))          # long 2 @ 100
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=1.0, liq_price=60.0, fee=0.5, trade_id="p1"))
    # only 1.0 closed: realized (60-100)*1 = -40 ; residual long 1.0 @ 100 ; fee -0.5
    assert acc.realized_pnl == -40.0
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 1.0
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["avg_px"] == 100.0
    assert acc.balance == -0.5


def test_apply_liquidation_two_partials_sum_to_full():
    """Two distinct partials summing to the held size flatten it; each realizes + charges its own fee."""
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 2.0, 100.0))          # long 2 @ 100
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=1.0, liq_price=60.0, fee=0.5, trade_id="p1"))
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=1.0, liq_price=60.0, fee=0.5, trade_id="p2"))
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0
    assert acc.realized_pnl == -80.0               # -40 + -40
    assert acc.balance == -1.0                     # 0.5 per partial, twice


def test_apply_liquidation_qty_exceeds_size_clamps_to_held():
    """An over-reported qty (> held) clamps to the held size — never flips the position negative."""
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 1.0, 100.0))          # long 1 @ 100
    acc.apply_liquidation(PositionLiquidated(
        venue="binance", symbol="BTCUSDT", position_side="BOTH",
        qty=5.0, liq_price=60.0, fee=0.0, trade_id="big"))
    assert acc.positions[("binance", "BTCUSDT", "BOTH")]["size"] == 0.0  # flat, not -4
    assert acc.realized_pnl == -40.0


def test_apply_liquidation_replay_does_not_double_charge_fee():
    """A reconnect-replayed PositionLiquidated must not deduct the fee (or realize PnL) twice."""
    acc = Account(venue="binance")
    acc.apply_fill(_fill(+1, 2.0, 100.0))          # long 2 @ 100
    liq = PositionLiquidated(venue="binance", symbol="BTCUSDT", position_side="BOTH",
                             qty=2.0, liq_price=60.0, fee=0.5)
    acc.apply_liquidation(liq)                      # real close: realized -80, fee -0.5
    assert acc.balance == -0.5
    assert acc.realized_pnl == -80.0
    acc.apply_liquidation(liq)                      # REPLAY (position now flat) -> true no-op
    assert acc.balance == -0.5                      # fee NOT double-charged
    assert acc.realized_pnl == -80.0               # PnL NOT double-realized
