"""Golden baseline: per-symbol contract multipliers in MultiSymbolEngine (approved fork 3).

BEFORE the S3 fix MSE applied ONE scalar `multiplier` to every symbol. AFTER the fix each leg is
valued by its own multiplier via `multiplier_of`. These numbers are computed by hand and are the
locked golden baseline for the mixed-multiplier case.
"""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine, PortfolioStrategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _series(opens):
    return [_bar(i * 60_000, o, o) for i, o in enumerate(opens)]


class _BuyBothHold(PortfolioStrategy):
    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("AAA", 1.0)
            self.buy("BBB", 1.0)


def test_mixed_multiplier_equity_values_each_leg_by_its_own_multiplier():
    bars = {"AAA": _series([100, 110, 120, 130]), "BBB": _series([10, 12, 14, 16])}
    eng = MultiSymbolEngine(bars, _BuyBothHold(), cash=10_000.0, multipliers={"AAA": 1.0, "BBB": 10.0})
    result = eng.run()
    # AAA bought 1 @ 110*1=110; BBB bought 1 @ 12*10=120; cash = 10000-110-120 = 9770
    # final: AAA 1*130*1=130 ; BBB 1*16*10=160 ; equity = 9770+130+160 = 10060
    assert eng.cash == pytest.approx(9_770.0)
    assert result.final_equity == pytest.approx(10_060.0)


def test_unlisted_symbol_falls_back_to_scalar_multiplier_default():
    bars = {"AAA": _series([100, 100, 100]), "CCC": _series([20, 20, 20])}
    eng = MultiSymbolEngine(bars, _BuyBothHold(), cash=10_000.0, multiplier=3.0, multipliers={"AAA": 1.0})
    assert eng.multiplier_of("AAA") == 1.0
    assert eng.multiplier_of("CCC") == 3.0   # unlisted -> scalar default
