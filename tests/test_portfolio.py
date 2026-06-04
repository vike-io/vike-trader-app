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


# ---------------------------------------------------------------------------
# W2-D: account-level leverage cap, liquidation, and per-symbol funding
# ---------------------------------------------------------------------------

def test_portfolio_leverage_caps_account_notional():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy

    def _b(ts, px):
        return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class OverBuy(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("A", 100.0)   # 100*100=10000 notional, but leverage 1 x 1000 equity = max 1000

    eng = PortfolioEngine({"A": [_b(1, 100.0), _b(2, 100.0)]}, OverBuy(), cash=1000.0, leverage=1.0)
    eng.run()
    # capped to ~10 units (1000 notional at price 100), not 100
    assert eng._pos["A"].size <= 10.0 + 1e-9
    assert eng._pos["A"].size > 0.0


def test_portfolio_liquidation_force_closes_underwater_position():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy

    class LongOnce(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("A", 10.0)

    # buy ~10 @100 with tiny cash so a crash wipes equity below maint margin -> liquidation
    bars = [Bar(ts=1, open=100, high=100, low=100, close=100, volume=1),
            Bar(ts=2, open=100, high=100, low=100, close=100, volume=1),   # fills @100
            Bar(ts=3, open=60, high=60, low=40, close=50, volume=1)]        # crash: adverse low 40
    eng = PortfolioEngine({"A": bars}, LongOnce(), cash=200.0, leverage=10.0, maint_margin=0.1)
    eng.run()
    assert eng._pos["A"].size == 0.0    # liquidated


def test_portfolio_funding_charged_per_symbol():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy

    class HoldLong(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("A", 1.0)

    # bar 2 has funding 0.01; holding 1 unit @ close 100 -> longs pay 1*100*0.01 = 1.0
    bars = [Bar(ts=1, open=100, high=100, low=100, close=100, volume=1),
            Bar(ts=2, open=100, high=100, low=100, close=100, volume=1, funding=0.01)]
    eng = PortfolioEngine({"A": bars}, HoldLong(), cash=1000.0)
    eng.run()
    # cash reduced by the 1.0 funding charge (no fees in this config)
    assert eng.cash < 1000.0 - 100.0   # paid 100 notional for the unit AND ~1.0 funding


def test_cash_gate_drops_unfundable_lower_weight_open():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy

    def _b(ts, px):
        return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class TwoBuys(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("A", 8.0, weight=10.0)   # high priority: 8*100=800 notional
                self.buy("B", 8.0, weight=1.0)    # low priority: another 800; only 1000 cash

    eng = PortfolioEngine({"A": [_b(1, 100.0), _b(2, 100.0)], "B": [_b(1, 100.0), _b(2, 100.0)]},
                          TwoBuys(), cash=1000.0, cash_gate=True)
    eng.run()
    assert eng._pos["A"].size == 8.0          # higher weight funded
    assert eng._pos["B"].size == 0.0          # lower weight dropped (insufficient cash)
    assert any(d[0] == "B" for d in eng.dropped)


def test_cash_gate_off_by_default_allows_negative_cash():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy

    def _b(ts, px):
        return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class TwoBuys(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("A", 8.0)
                self.buy("B", 8.0)

    eng = PortfolioEngine({"A": [_b(1, 100.0), _b(2, 100.0)], "B": [_b(1, 100.0), _b(2, 100.0)]},
                          TwoBuys(), cash=1000.0)   # cash_gate default False
    eng.run()
    assert eng._pos["A"].size == 8.0 and eng._pos["B"].size == 8.0   # both fill, cash goes negative
    assert eng.cash < 0.0


# ---------------------------------------------------------------------------
# F3: per-symbol PnL curves
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# max_open_long / max_open_short caps
# ---------------------------------------------------------------------------

class _BothBuy(PortfolioStrategy):
    """Both symbols attempt to open longs at step 0."""
    def on_bar(self, ts, bars):
        if self.index == 0:
            for sym in bars:
                self._engine.submit(sym, +1, 1.0)


def test_max_open_long_blocks_second_long():
    """With max_open_long=1, two symbols both trying to open longs -> only one opens."""
    bars = {
        "AAA": _series([100, 100, 100]),
        "BBB": _series([100, 100, 100]),
    }
    eng = PortfolioEngine(bars, _BothBuy(), cash=100_000.0, max_open_long=1)
    eng.run()
    open_longs = sum(1 for s in ("AAA", "BBB") if eng._pos[s].size > 0)
    assert open_longs == 1


class _BothSell(PortfolioStrategy):
    """Both symbols attempt to open shorts at step 0."""
    def on_bar(self, ts, bars):
        if self.index == 0:
            for sym in bars:
                self._engine.submit(sym, -1, 1.0)


def test_max_open_short_blocks_second_short():
    """With max_open_short=1, two symbols both trying to open shorts -> only one opens."""
    bars = {
        "AAA": _series([100, 100, 100]),
        "BBB": _series([100, 100, 100]),
    }
    eng = PortfolioEngine(bars, _BothSell(), cash=100_000.0, max_open_short=1)
    eng.run()
    open_shorts = sum(1 for s in ("AAA", "BBB") if eng._pos[s].size < 0)
    assert open_shorts == 1


def test_max_open_long_does_not_block_shorts():
    """max_open_long=1 must NOT block short entries (different direction)."""
    class _LongAndShort(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self._engine.submit("AAA", +1, 1.0)  # long
                self._engine.submit("BBB", -1, 1.0)  # short

    bars = {
        "AAA": _series([100, 100, 100]),
        "BBB": _series([100, 100, 100]),
    }
    eng = PortfolioEngine(bars, _LongAndShort(), cash=100_000.0, max_open_long=1)
    eng.run()
    assert eng._pos["AAA"].size > 0  # long opened
    assert eng._pos["BBB"].size < 0  # short also opened (different cap)


def test_long_short_caps_zero_means_no_limit():
    """Default (0) must impose no limit — all positions open freely."""
    bars = {
        "AAA": _series([100, 100, 100]),
        "BBB": _series([100, 100, 100]),
        "CCC": _series([100, 100, 100]),
    }

    class _AllBuy(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                for sym in bars:
                    self._engine.submit(sym, +1, 1.0)

    eng = PortfolioEngine(bars, _AllBuy(), cash=100_000.0)  # max_open_long=0 by default
    eng.run()
    assert all(eng._pos[s].size > 0 for s in ("AAA", "BBB", "CCC"))


def test_per_symbol_curves_length_and_last_value():
    """per_symbol_curves has one entry per symbol, each of length == number of bars,
    and the last value of each curve matches per_symbol_pnl[s]."""

    class _BuyA(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self.buy("AAA", 1.0)

    n_bars = 4
    bars = {
        "AAA": _series([100, 110, 120, 130]),
        "BBB": _series([10, 10, 10, 10]),
    }
    eng = PortfolioEngine(bars, _BuyA(), cash=10_000.0)
    result = eng.run()

    assert result.per_symbol_curves is not None
    for sym in ("AAA", "BBB"):
        curve = result.per_symbol_curves[sym]
        # one entry per bar
        assert len(curve) == n_bars, f"{sym} curve length {len(curve)} != {n_bars}"
        # last value matches per_symbol_pnl
        assert abs(curve[-1] - result.per_symbol_pnl[sym]) < 1e-9, (
            f"{sym}: curve[-1]={curve[-1]} != per_symbol_pnl={result.per_symbol_pnl[sym]}"
        )
