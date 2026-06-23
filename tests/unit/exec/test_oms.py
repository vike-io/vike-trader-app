"""OmsHub: the paper composition root — engine(risk)+SimClient+Account+bus wired into one path."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.exec.oms import OmsHub
from vike_trader_app.exec.risk import RiskGate, RiskLimits


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


def _drive(oms):
    for bar in _bars():
        oms.on_bar_live(bar)
    return oms


def test_account_reconciles_with_the_tester_result():
    # no seed -> every fill is live -> the FillEvent-derived Account equals the engine's trades
    oms = _drive(OmsHub(symbol="X", interval="1m", strategy=_BuyThenClose(), cash=10_000.0, taker_fee=0.001))
    res = oms.result()
    assert oms.account.trades == [t.pnl for t in res.trades]
    assert oms.account.positions.get(("sim", "X", "BOTH"), {"size": 0.0})["size"] == 0.0  # flat at end


def test_omshub_is_duck_compatible_with_paper_tester():
    oms = _drive(OmsHub(symbol="BTCUSDT", interval="1m", strategy=_BuyThenClose(), cash=10_000.0))
    res = oms.result()
    assert len(res.trades) == 1 and res.trades[0].entry_price == 110.0 and res.trades[0].exit_price == 130.0
    assert len(oms.equity_curve) == 4


def test_risk_gate_in_the_oms_denies_an_over_notional_open():
    oms = _drive(OmsHub(symbol="X", interval="1m", strategy=_BuyThenClose(), cash=10_000.0,
                        risk=RiskGate(RiskLimits(max_notional_per_order=10.0))))
    assert oms.result().trades == []           # the buy was gated out
    assert oms.account.trades == []            # and the Account saw no fills


def test_seed_warmup_fills_are_not_in_the_live_account():
    # the sim client attaches AFTER warm-up, so a trade during seed never reaches the live Account
    class _BuyOnSeed(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0)
    oms = OmsHub(symbol="X", interval="1m", strategy=_BuyOnSeed(), cash=10_000.0, seed_bars=_bars()[:2])
    for bar in _bars()[2:]:
        oms.on_bar_live(bar)
    # the buy (and its fill) happened during warm-up -> not on the bus -> Account never opened a position
    assert oms.account.positions.get(("sim", "X", "BOTH"), {"size": 0.0})["size"] == 0.0
