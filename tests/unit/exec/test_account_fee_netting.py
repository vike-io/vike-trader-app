from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.events import FillEvent


def _fill(side, qty, px, commission):
    return FillEvent(trade_id=f"t{px}{commission}", client_order_id="c1", venue="binance",
                     symbol="BTCUSDT", side=side, last_qty=qty, last_px=px, commission=commission)


def test_charge_lowers_balance_and_tracks_fees():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, commission=0.5))   # buy, 0.5 charge
    assert acc.balance == -0.5
    assert acc.fees_paid == 0.5
    assert acc.realized_pnl == 0.0           # gross PnL unchanged (no close yet)


def test_rebate_raises_balance():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, commission=-0.2))  # maker rebate (negative = income)
    assert acc.balance == 0.2
    assert acc.fees_paid == -0.2


def test_realized_pnl_stays_gross_balance_carries_fees():
    acc = Account()
    acc.apply_fill(_fill(+1, 1.0, 100.0, commission=0.1))   # open long
    acc.apply_fill(_fill(-1, 1.0, 110.0, commission=0.1))   # close +10 gross
    assert acc.realized_pnl == 10.0          # gross, fees NOT netted into PnL
    assert acc.balance == -0.2               # both commissions netted into balance
    assert acc.fees_paid == 0.2
