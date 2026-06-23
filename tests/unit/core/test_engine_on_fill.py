"""Optional on_fill hook on BacktestEngine — fires per fill; default-off is byte-identical."""

from vike_trader_app.core.broker_sim import fee as _fee
from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _bars():
    return [_bar(0, 100, 100), _bar(60_000, 110, 110), _bar(120_000, 120, 120), _bar(180_000, 130, 130)]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


def test_on_fill_fires_once_per_fill_with_adverse_price_and_fee():
    fills = []
    eng = BacktestEngine(
        _bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001,
        on_fill=lambda side, size, price, fee, ts, is_maker:
            fills.append((side, size, price, fee, ts, is_maker)),
    )
    eng.run()
    # buy 1 fills at the NEXT open (110, taker); close (sell 1) fills at the next open (130, taker)
    assert [(f[0], f[1], f[2], f[5]) for f in fills] == [
        (+1, 1.0, 110.0, False),
        (-1, 1.0, 130.0, False),
    ]
    assert fills[0][3] == _fee(1.0, 110.0, 0.001, 1.0)
    assert fills[1][3] == _fee(1.0, 130.0, 0.001, 1.0)
    assert [f[4] for f in fills] == [60_000, 180_000]  # fill bar timestamps


def test_default_off_hook_is_byte_identical():
    base = BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001).run()
    hooked = BacktestEngine(_bars(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001,
                            on_fill=lambda *a: None).run()
    assert hooked.equity_curve == base.equity_curve
    assert [t.pnl for t in hooked.trades] == [t.pnl for t in base.trades]
    assert hooked.final_equity == base.final_equity
