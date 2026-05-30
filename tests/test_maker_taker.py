"""Maker/taker fee tiers: limit fills pay maker, market/stop/trailing pay taker."""

import pytest

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.strategy import Strategy


def _bar(ts, o):
    return Bar(ts=ts, open=o, high=o + 1, low=o - 1, close=o, volume=1.0)


def _flat_bars():
    return [_bar(i * 60_000, 100.0) for i in range(4)]  # open == 100 every bar


class _MarketRoundTrip(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)     # taker
        elif self.index == 2:
            self.close()      # taker


class _LimitEntryMarketExit(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.limit_buy(1.0, 100.0)  # maker (rests, fills at 100)
        elif self.index == 2:
            self.close()                # taker


def test_market_round_trip_pays_taker_both_legs():
    r = BacktestEngine(_flat_bars(), _MarketRoundTrip(), maker_fee=0.001, taker_fee=0.002).run()
    # both legs taker @100: 100*0.002 + 100*0.002
    assert r.trades[0].fees == pytest.approx(0.2 + 0.2)


def test_limit_entry_pays_maker_then_taker_exit():
    r = BacktestEngine(_flat_bars(), _LimitEntryMarketExit(), maker_fee=0.001, taker_fee=0.002).run()
    # entry maker @100 (0.1) + exit taker @100 (0.2)
    assert r.trades[0].fees == pytest.approx(0.1 + 0.2)


def test_fee_rate_still_applies_to_both_when_tiers_unset():
    r = BacktestEngine(_flat_bars(), _MarketRoundTrip(), fee_rate=0.001).run()
    assert r.trades[0].fees == pytest.approx(0.1 + 0.1)  # fee_rate used for both legs
