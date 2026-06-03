"""Multi-asset / portfolio backtesting tests."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c, volume=1.0)


def _series(opens):
    """One symbol's bars where open == close == each value (simple, deterministic)."""
    return [_bar(i * 60_000, o, o) for i, o in enumerate(opens)]


class _BuyAThenClose(PortfolioStrategy):
    """Buy 1 unit of AAA at step 0, close it at step 2."""

    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("AAA", 1.0)
        elif self.index == 2:
            self.close("AAA")


def test_portfolio_buy_then_close_fills_at_next_open():
    bars = {
        "AAA": _series([100, 110, 120, 130]),  # opens 100,110,120,130
        "BBB": _series([10, 10, 10, 10]),
    }
    eng = PortfolioEngine(bars, _BuyAThenClose(), cash=10_000.0)
    result = eng.run()
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.entry_price == 110.0  # next open after step-0 buy
    assert t.exit_price == 130.0  # next open after step-2 close
    assert t.pnl == 20.0
    assert result.final_equity == pytest.approx(10_020.0)


def test_portfolio_unaligned_series_rejected():
    bars = {"AAA": _series([1, 2, 3]), "BBB": _series([1, 2])}
    with pytest.raises(ValueError):
        PortfolioEngine(bars, _BuyAThenClose())


class _ShortBClose(PortfolioStrategy):
    def on_bar(self, ts, bars):
        if self.index == 0:
            self.sell("BBB", 2.0)  # open short -> fills next open
        elif self.index == 2:
            self.close("BBB")


def test_portfolio_fees_and_short():
    bars = {
        "AAA": _series([100, 100, 100, 100]),
        "BBB": _series([50, 60, 70, 40]),  # opens 50,60,70,40
    }
    eng = PortfolioEngine(bars, _ShortBClose(), fee_rate=0.001, cash=10_000.0)
    result = eng.run()
    assert len(result.trades) == 1
    t = result.trades[0]
    # short opened at next open after step0 = 60; closed at next open after step2 = 40
    assert t.entry_price == 60.0
    assert t.exit_price == 40.0
    # short PnL: signed size = -2 -> (40-60)*(-2) = +40
    assert t.pnl == pytest.approx(40.0)
    assert t.fees == pytest.approx(2.0 * 60 * 0.001 + 2.0 * 40 * 0.001)  # 0.12 + 0.08
    assert result.final_equity == pytest.approx(10_000 + 40 - 0.20)


class _EqualWeightOnce(PortfolioStrategy):
    """At step 0, target 50/50 across AAA and BBB; then hold."""

    def on_bar(self, ts, bars):
        if self.index == 0:
            self.rebalance({"AAA": 0.5, "BBB": 0.5})


def test_order_target_percent_allocates_half_equity_each():
    # constant prices so sizing is exact and stable
    bars = {
        "AAA": _series([100, 100, 100]),
        "BBB": _series([25, 25, 25]),
    }
    eng = PortfolioEngine(bars, _EqualWeightOnce(), cash=10_000.0)
    result = eng.run()
    # 50% of 10k = 5000 each. AAA: 5000/100 = 50 units; BBB: 5000/25 = 200 units.
    assert eng.position_of("AAA").size == pytest.approx(50.0)
    assert eng.position_of("BBB").size == pytest.approx(200.0)
    # fully invested, constant prices -> equity unchanged
    assert result.final_equity == pytest.approx(10_000.0)


class _PeriodicEqualWeight(PortfolioStrategy):
    """Rebalance to equal weight every ``every`` steps."""

    every = 3

    def on_bar(self, ts, bars):
        if self.index % self.every == 0:
            syms = list(bars)
            w = 1.0 / len(syms)
            self.rebalance({s: w for s in syms})


def test_periodic_equal_weight_rebalance_runs_and_stays_invested():
    bars = {
        "AAA": _series([100, 102, 101, 103, 105, 104]),
        "BBB": _series([50, 49, 51, 52, 50, 53]),
        "CCC": _series([10, 11, 10, 12, 11, 13]),
    }
    eng = PortfolioEngine(bars, _PeriodicEqualWeight(), fee_rate=0.0005, cash=100_000.0)
    result = eng.run()
    # All three carry a positive position by the end (fully invested, long-only weights).
    assert all(eng.position_of(s).size > 0 for s in ("AAA", "BBB", "CCC"))
    assert len(result.equity_curve) == 6
    assert result.final_equity > 0


class _ScaleOut(PortfolioStrategy):
    """Buy 10 AAA at step 0, sell 4 (partial) at step 2."""

    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("AAA", 10.0)
        elif self.index == 2:
            self.sell("AAA", 4.0)


def test_partial_scale_out_keeps_remainder_and_realizes_part():
    bars = {"AAA": _series([100, 100, 150, 150])}  # buy fills @100, partial sell @150
    eng = PortfolioEngine(bars, _ScaleOut(), cash=10_000.0)
    result = eng.run()
    # 4 units closed at 150 from cost 100 -> realized pnl = (150-100)*4 = 200
    assert len(result.trades) == 1
    assert result.trades[0].size == 4.0
    assert result.trades[0].pnl == pytest.approx(200.0)
    # 6 units remain open at cost basis 100, marked at 150
    assert eng.position_of("AAA").size == pytest.approx(6.0)
    assert eng.position_of("AAA").avg_price == pytest.approx(100.0)
    # equity = cash + 6*150. cash = 10000 -10*100 (buy) +4*150 (sell) = 9600; +900 = 10500
    assert result.final_equity == pytest.approx(10_500.0)


def test_portfolio_trades_are_tagged_with_symbol():
    def _b(ts, px):
        return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class Trader(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("A", 1.0)
            elif self.index == 2:
                self.close("A")

    eng = PortfolioEngine({"A": [_b(1, 10.0), _b(2, 11.0), _b(3, 12.0), _b(4, 13.0)]}, Trader(), cash=1000.0)
    result = eng.run()
    assert result.trades, "expected a completed round-trip"
    assert all(t.symbol == "A" for t in result.trades)
