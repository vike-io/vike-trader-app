"""OmsHub: the paper composition root — engine(risk)+SimClient+Account+bus wired into one path."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.exec.events import FillEvent
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
    # no seed -> seed_trade_count is 0 -> the FillEvent-derived Account equals the engine's full trades
    oms = _drive(OmsHub(symbol="X", interval="1m", strategy=_BuyThenClose(), cash=10_000.0))
    res = oms.result()
    assert oms.seed_trade_count == 0
    assert oms.account.trades == [t.pnl for t in res.trades[oms.seed_trade_count:]]
    assert oms.account.positions.get(("sim", "X", "BOTH"), {"size": 0.0})["size"] == 0.0  # flat at end


def test_account_reconciles_against_the_live_portion_when_a_roundtrip_closes_in_seed():
    # A round-trip that OPENS and CLOSES during seed warm-up is recorded in engine.trades (returned by
    # result()) but the Account — attached after warm-up — never saw it. So the invariant is against the
    # LIVE portion result().trades[seed_trade_count:], NOT all of result().trades.
    class _RoundTripInSeedThenLive(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0)      # opens; fills at seed bar 1's open
            elif self.index == 1:
                self.close()       # closes; fills at seed bar 2's open -> round-trip completes in warm-up
            elif self.index == 3:
                self.buy(1.0)      # opens live
            elif self.index == 4:
                self.close()       # closes live

    seed = [_bar(0, 100, 100), _bar(60_000, 110, 110), _bar(120_000, 120, 120)]        # idx 0,1,2
    live = [_bar(180_000, 130, 130), _bar(240_000, 140, 140), _bar(300_000, 150, 150)]  # idx 3,4,5
    oms = OmsHub(symbol="X", interval="1m", strategy=_RoundTripInSeedThenLive(), cash=10_000.0, seed_bars=seed)
    for bar in live:
        oms.on_bar_live(bar)
    res = oms.result()
    assert oms.seed_trade_count == 1                       # the seed round-trip is recorded pre-Account
    assert len(res.trades) == 2                            # + the live round-trip
    # the headline invariant: the Account reconciles with the LIVE portion, not the full engine trades
    assert oms.account.trades == [t.pnl for t in res.trades[oms.seed_trade_count:]]
    assert oms.account.trades == [t.pnl for t in oms.live_trades]     # live_trades helper agrees
    assert oms.account.trades != [t.pnl for t in res.trades]          # divergence from the full list is real


def test_fill_event_commissions_propagate_from_the_engine_fee():
    # the SimClient stamps the engine's per-fill fee onto FillEvent.commission; the Account folds GROSS
    # PnL and discards commission, so the fee path needs its own assertion. Net equity = cash + gross - fees.
    fills = []
    oms = OmsHub(symbol="X", interval="1m", strategy=_BuyThenClose(), cash=10_000.0, taker_fee=0.001)
    oms.bus.subscribe(lambda ev: fills.append(ev) if isinstance(ev, FillEvent) else None)
    _drive(oms)
    res = oms.result()
    commissions = sum(f.commission for f in fills)
    assert commissions > 0.0                                            # fees were actually charged
    assert oms.account.trades == [t.pnl for t in res.trades]            # account PnL is GROSS (fee not netted)
    assert abs((10_000.0 + sum(oms.account.trades) - commissions) - res.final_equity) < 1e-9


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
    # the engine DID open during warm-up (buy@0 fills at the 2nd seed bar's open) ...
    assert oms.engine.position.size == 1.0
    # ... but that fill executed before the client attached -> not on the bus -> the Account stays flat
    assert oms.account.positions.get(("sim", "X", "BOTH"), {"size": 0.0})["size"] == 0.0
