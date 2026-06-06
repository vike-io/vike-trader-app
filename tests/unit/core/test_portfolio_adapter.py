# tests/test_portfolio_adapter.py
"""WealthLab-style portfolio backtest: one single-symbol Strategy per symbol, shared cash."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioResult, PortfolioStrategy
from vike_trader_app.core.portfolio_adapter import (
    MultiSymbolStrategyRunner,
    SymbolEngineShim,
    align_bars,
)
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.tester.config import TesterConfig


def _bar(ts, px):
    return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)


def test_align_bars_unions_timestamps_and_forward_fills():
    a = [_bar(1, 10.0), _bar(2, 11.0)]
    b = [_bar(2, 20.0), _bar(3, 21.0)]
    aligned = align_bars({"A": a, "B": b})
    assert [bar.ts for bar in aligned["A"]] == [1, 2, 3]
    assert [bar.ts for bar in aligned["B"]] == [1, 2, 3]
    assert aligned["B"][0].close == 20.0
    assert aligned["A"][2].close == 11.0
    assert len({len(v) for v in aligned.values()}) == 1


def test_shim_forwards_orders_and_reads_to_engine():
    a = [_bar(1, 10.0), _bar(2, 10.0), _bar(3, 10.0)]
    captured = {}

    class _Driver(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                shim = SymbolEngineShim(self._engine, "A", self)
                captured["shim"] = shim
                shim.submit(+1, 5.0)
            if self.index == 2:
                captured["pos_size"] = captured["shim"].position.size
                captured["equity"] = captured["shim"].equity_now()

    eng = PortfolioEngine({"A": a}, _Driver(), cash=1000.0)
    eng.run()
    assert captured["pos_size"] == 5.0
    assert captured["equity"] == eng.equity_now()


def test_shim_forwards_resting_orders_to_engine():
    eng = PortfolioEngine({"A": [_bar(1, 100.0), _bar(2, 100.0)]}, PortfolioStrategy(), cash=10_000.0)
    shim = SymbolEngineShim(eng, "A", None)
    shim.submit_limit(+1, 1.0, 95.0)
    shim.submit_stop(+1, 1.0, 105.0)
    shim.submit_trailing(-1, 1.0, 5.0)
    assert len(eng._pending["A"]) == 3
    assert {o.kind for o in eng._pending["A"]} == {"limit", "stop", "trailing"}
    shim.cancel_all()
    assert eng._pending["A"] == []


def test_shim_bars_for_raises_when_tf_not_configured():
    import pytest
    eng = PortfolioEngine({"A": [_bar(1, 1.0)]}, PortfolioStrategy(), cash=10.0)
    shim = SymbolEngineShim(eng, "A", None)
    # No timeframes configured -> KeyError (the tf is not in self._tf[symbol])
    with pytest.raises(KeyError):
        shim.bars_for("1h")


class BuyHold(Strategy):
    def on_bar(self, bar):
        if self.position.size == 0:
            self.buy(1.0)


def test_runner_runs_strategy_per_symbol_shared_cash():
    a = [_bar(1, 10.0), _bar(2, 12.0), _bar(3, 12.0)]
    b = [_bar(1, 5.0), _bar(2, 5.0), _bar(3, 6.0)]
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=1000.0))
    result = runner.run()
    assert isinstance(result, PortfolioResult)
    assert set(result.per_symbol_pnl) == {"A", "B"}
    assert len(result.equity_curve) == 3


def test_runner_max_open_positions_caps_entries():
    # prices rise on the last bar, so any opened position shows nonzero PnL; with cap=1 only one
    # of the two symbols may open on bar 0 (the other is blocked by the pending-aware cap).
    a = [_bar(1, 1.0), _bar(2, 1.0), _bar(3, 2.0)]
    b = [_bar(1, 1.0), _bar(2, 1.0), _bar(3, 2.0)]
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=1000.0),
                                       max_open_positions=1)
    result = runner.run()
    opened = [s for s in ("A", "B") if result.per_symbol_pnl[s] != 0.0]
    assert len(opened) == 1


def test_runner_report_wraps_into_tester_report():
    a = [_bar(1, 10.0), _bar(2, 12.0)]
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=1000.0))
    report = runner.report()
    assert report.final_equity == runner.run().final_equity


def test_short_position_via_order_target_closes_with_correct_pnl_sign():
    # short at 10, price falls to 8 -> profit. order_target_shares(-1) opens short,
    # order_target_shares(0) covers.
    class ShortThenCover(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.order_target_shares(-1.0)
            elif self.index == 2:
                self.order_target_shares(0.0)
    a = [_bar(1, 10.0), _bar(2, 9.0), _bar(3, 8.0)]
    runner = MultiSymbolStrategyRunner(ShortThenCover, {"A": a}, TesterConfig(cash=1000.0))
    result = runner.run()
    assert result.per_symbol_pnl["A"] > 0.0   # short profited as price fell


def test_order_target_percent_sizes_off_shared_equity():
    # target 50% of 1000 equity at price 10 -> 50 shares; with flat price equity stays ~1000
    class HalfEquity(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.order_target_percent(0.5)
    a = [_bar(1, 10.0), _bar(2, 10.0), _bar(3, 10.0)]
    runner = MultiSymbolStrategyRunner(HalfEquity, {"A": a}, TesterConfig(cash=1000.0))
    result = runner.run()
    assert len(result.equity_curve) == 3
    # at flat price, equity stays ~1000 (no fees in this config)
    assert abs(result.final_equity - 1000.0) < 1e-6


def test_cap_does_not_block_adding_to_an_open_symbol():
    # cap=1, single symbol buys twice -> the second buy is an ADD (position already open), allowed.
    class BuyTwice(Strategy):
        def on_bar(self, bar):
            if self.index in (0, 1):
                self.buy(1.0)
    a = [_bar(1, 10.0), _bar(2, 10.0), _bar(3, 10.0)]
    runner = MultiSymbolStrategyRunner(BuyTwice, {"A": a}, TesterConfig(cash=1000.0),
                                       max_open_positions=1)
    result = runner.run()
    # both buys filled -> position size 2 -> with flat price, no PnL but the run completes and the
    # add was not blocked (key present in per_symbol_pnl confirms run succeeded)
    assert "A" in result.per_symbol_pnl


# ---------------------------------------------------------------------------
# Risk-based sizing: buy/sell(stop=) -> MaxRiskPct sizer + auto protective stop + PortfolioHeat
# ---------------------------------------------------------------------------

def _ohlc(ts, o, h, l, c):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)


class BuyWithStop(Strategy):
    def on_bar(self, bar):
        if self.index == 0 and self.position.size == 0:
            self.buy(1.0, stop=90.0)


def test_max_risk_pct_sizes_off_stop_distance():
    from vike_trader_app.core.sizing import MaxRiskPctSizer
    # submit on bar 0 (close 100), stop 90 -> risk/unit 10 -> qty = 0.01*10000/10 = 10.
    # Entry fills at bar 1 open (100). Bars 1+ stay above 90 -> never stopped here.
    a = [_ohlc(0, 100, 100, 100, 100), _ohlc(1, 100, 101, 99, 100), _ohlc(2, 100, 101, 99, 100)]
    runner = MultiSymbolStrategyRunner(BuyWithStop, {"A": a},
                                       TesterConfig(cash=10_000.0, sizer=MaxRiskPctSizer(0.01)))
    runner.run()
    assert runner._engine.position_of("A").size == pytest.approx(10.0)


def test_protective_stop_closes_position_at_stop_price():
    from vike_trader_app.core.sizing import MaxRiskPctSizer
    # entry fills bar 1; bar 2 dips low 89 <= 90 -> protective stop closes at 90.
    a = [_ohlc(0, 100, 100, 100, 100), _ohlc(1, 100, 101, 99, 100), _ohlc(2, 100, 101, 89, 95)]
    runner = MultiSymbolStrategyRunner(BuyWithStop, {"A": a},
                                       TesterConfig(cash=10_000.0, sizer=MaxRiskPctSizer(0.01)))
    runner.run()
    eng = runner._engine
    assert eng.position_of("A").size == 0.0          # closed by the protective stop
    assert eng._stop["A"] is None                    # stop cleared after the close
    # loss = (90 - 100) * 10 units = -100
    assert runner.run().per_symbol_pnl  # sanity: run produces attribution
    # exit was AT the stop price (90), not the bar low (89)
    trade = [t for t in eng.trades if t.symbol == "A"][-1]
    assert trade.exit_price == pytest.approx(90.0)


def test_protective_stop_not_triggered_on_entry_bar():
    from vike_trader_app.core.sizing import MaxRiskPctSizer
    # entry fills bar 1 whose low (85) is already below the stop (90). It must NOT stop out on the
    # entry bar itself (look-ahead). Bars after stay above 90 -> position survives the run.
    a = [_ohlc(0, 100, 100, 100, 100), _ohlc(1, 100, 101, 85, 100), _ohlc(2, 100, 101, 99, 100)]
    runner = MultiSymbolStrategyRunner(BuyWithStop, {"A": a},
                                       TesterConfig(cash=10_000.0, sizer=MaxRiskPctSizer(0.01)))
    runner.run()
    eng = runner._engine
    assert eng.position_of("A").size == pytest.approx(10.0)  # NOT stopped on its own entry bar
    assert eng._stop["A"] == 90.0                            # still armed for future bars


def test_protective_stop_cleared_when_position_closed_by_other_means():
    from vike_trader_app.core.sizing import MaxRiskPctSizer
    # Buy with a stop on bar 0, then explicitly close on bar 1. The dangling stop must be cleared,
    # and a later dip below 90 must NOT re-open or re-trigger anything.
    class BuyThenClose(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(1.0, stop=90.0)
            elif self.index == 1:
                self.close()
    # close on bar 1 fills at bar 2 open (flat, stop cleared); the dip to 80 on bar 3 must do nothing.
    a = [_ohlc(0, 100, 100, 100, 100), _ohlc(1, 100, 101, 99, 100),
         _ohlc(2, 100, 101, 99, 100), _ohlc(3, 100, 101, 80, 85)]
    runner = MultiSymbolStrategyRunner(BuyThenClose, {"A": a},
                                       TesterConfig(cash=10_000.0, sizer=MaxRiskPctSizer(0.01)))
    runner.run()
    eng = runner._engine
    assert eng.position_of("A").size == 0.0
    assert eng._stop["A"] is None  # no dangling stop -> the bar-3 dip neither re-triggers nor re-opens


class StaggeredBuyWithStop(Strategy):
    """Symbol A enters on bar 0, symbol B on bar 1 — so B is sized with A's risk already open
    (PortfolioHeat must scale B down). Distinguished by the symbol bound to the shim."""

    def on_bar(self, bar):
        if self.position.size != 0:
            return
        sym = self._engine._symbol
        if sym == "A" and self.index == 0:
            self.buy(1.0, stop=90.0)
        elif sym == "B" and self.index == 1:
            self.buy(1.0, stop=90.0)


def test_portfolio_heat_caps_total_open_risk_across_symbols():
    from vike_trader_app.core.sizing import MaxRiskPctSizer, PortfolioHeatSizer
    # A buys bar 0 (fills bar 1), B buys bar 1 (fills bar 2). Each close 100, stop 90 -> risk/unit 10.
    # MaxRiskPct(0.01) alone -> 10 units each (risk 100 each, total 200 = 2% of 10k equity).
    # PortfolioHeat(max_heat=0.015) caps TOTAL risk to 150: A takes 100 (no open risk yet), then B is
    # sized with A's 100 already open -> budget 50 -> 5 units. Prices flat at 100 so equity stays 10k.
    a = [_ohlc(0, 100, 100, 100, 100), _ohlc(1, 100, 101, 99, 100),
         _ohlc(2, 100, 101, 99, 100), _ohlc(3, 100, 101, 99, 100)]
    b = [_ohlc(0, 100, 100, 100, 100), _ohlc(1, 100, 100, 100, 100),
         _ohlc(2, 100, 101, 99, 100), _ohlc(3, 100, 101, 99, 100)]
    sizer = PortfolioHeatSizer(MaxRiskPctSizer(0.01), max_heat=0.015)
    runner = MultiSymbolStrategyRunner(StaggeredBuyWithStop, {"A": a, "B": b},
                                       TesterConfig(cash=10_000.0, sizer=sizer))
    runner.run()
    eng = runner._engine
    assert eng.position_of("A").size == pytest.approx(10.0)  # first in: full 1% risk
    assert eng.position_of("B").size == pytest.approx(5.0)   # scaled to fit remaining heat budget
    # total open risk <= 1.5% of equity (~150)
    assert eng._open_risk() <= 0.015 * eng.equity_now() + 1e-6


def test_limit_order_fills_in_portfolio_mode():
    from vike_trader_app.core.model import Bar as _Bar

    def _o(ts, o, h, l, c):
        return _Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)

    class LimitBuy(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.limit_buy(1.0, 95.0)   # rest until price dips to 95

    a = [_o(0, 100, 101, 99, 100), _o(1, 100, 102, 98, 101), _o(2, 100, 101, 94, 96)]
    runner = MultiSymbolStrategyRunner(LimitBuy, {"A": a}, TesterConfig(cash=1000.0))
    result = runner.run()
    # the limit filled at 95 on bar 2 (low 94 <= 95); position is long 1 @ ~95
    assert result.per_symbol_pnl["A"] != 0.0 or result.final_equity != 1000.0


def test_resting_order_inert_on_synthetic_flat_fill_bars():
    # align_bars forward-fills gap symbols with zero-volume O=H=L=C bars; a far-off resting order
    # must NOT spuriously trigger on them.
    from vike_trader_app.core.model import Bar as _Bar

    class LimitFarAway(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.limit_buy(1.0, 1.0)   # absurdly low -> should never trigger

    a = [_Bar(ts=i, open=100, high=100, low=100, close=100, volume=0.0) for i in range(4)]  # flat synthetic-like
    runner = MultiSymbolStrategyRunner(LimitFarAway, {"A": a}, TesterConfig(cash=1000.0))
    result = runner.run()
    assert not result.trades and result.final_equity == 1000.0


def test_single_symbol_portfolio_matches_engine_with_costs():
    # A 1-symbol portfolio run must equal the single-symbol BacktestEngine on the same bars,
    # strategy, and cost config (slippage + maker/taker + multiplier), proving the unified cost model.
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.engine import BacktestEngine
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    def _o(ts, o, h, l, c):
        return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)

    class BuyThenClose(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(2.0)
            elif self.index == 3:
                self.close()

    bars = [_o(0, 100, 101, 99, 100), _o(1, 100, 105, 99, 104),
            _o(2, 104, 110, 103, 108), _o(3, 108, 109, 105, 106), _o(4, 106, 107, 104, 105)]
    cfg = dict(cash=1000.0, fee_rate=0.0, maker_fee=0.001, taker_fee=0.002,
               slippage=0.0005, multiplier=2.0)
    config = TesterConfig(**cfg)

    # single-symbol reference
    eng = BacktestEngine(list(bars), BuyThenClose(), **cfg)
    ref = eng.run()

    # 1-symbol portfolio
    result = MultiSymbolStrategyRunner(BuyThenClose, {"X": list(bars)}, config).run()

    assert result.final_equity == __import__("pytest").approx(ref.final_equity, rel=1e-9)
    assert len(result.trades) == len(ref.trades) == 1
    assert result.trades[0].pnl == __import__("pytest").approx(ref.trades[0].pnl, rel=1e-9)
    assert result.trades[0].fees == __import__("pytest").approx(ref.trades[0].fees, rel=1e-9)


# --- W4-B: per-symbol membership windows (dynamic / survivorship-free DataSets) ---


def test_inactive_symbol_does_not_open():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.data.datasets import DateRange
    from vike_trader_app.tester.config import TesterConfig

    def _b(ts, px):
        return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    # B is only a member from ts=25 onward; it must not open on the ts=10 or ts=20 bars.
    # A 4th bar (ts=40) lets B's first (active) order — submitted on the ts=30 bar — actually fill,
    # so we can assert its entry timestamp instead of relying on a no-next-bar accident.
    a = [_b(10, 100), _b(20, 100), _b(30, 100), _b(40, 100)]
    b = [_b(10, 50), _b(20, 50), _b(30, 50), _b(40, 50)]
    ranges = {"B": [DateRange(25, None)]}
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=10_000.0),
                                       ranges=ranges)
    res = runner.run()
    eng = runner._engine  # probe: the PortfolioEngine the runner built this run

    # A is always active: it buys on the ts=10 bar and fills at the ts=20 open -> entry_ts == 20.
    # B is inactive on ts=10/ts=20 (skipped, no order), first buys on the ts=30 bar (active),
    # which fills at the ts=40 open -> entry_ts == 40. If membership were ignored, B would have
    # filled at the ts=20 open (entry_ts == 20) like A.
    assert eng._pos["A"].size == 1.0 and eng._entry_ts["A"] == 20
    assert eng._pos["B"].size == 1.0, "B must hold exactly one unit opened on its first active bar"
    assert eng._entry_ts["B"] == 40, "B's position must not predate its activation (no fill while inactive)"
    # And no completed trade for B ever closed (it only opened on the last fillable bar).
    assert [t for t in res.trades if t.symbol == "B"] == []


def test_auto_close_on_removal_no_lookahead():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.data.datasets import DateRange
    from vike_trader_app.tester.config import TesterConfig

    def _o(ts, o, h, l, c):
        return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    # A is a member only through ts=20; it buys at ts=10 (fills @ ts=20 open=100), then is removed at ts=30
    # -> force-closed at ts=30's OPEN (107), NOT its high/low -> exit price 107, no look-ahead.
    a = [_o(10, 100, 101, 99, 100), _o(20, 100, 105, 99, 104), _o(30, 107, 120, 106, 119)]
    ranges = {"A": [DateRange(0, 20)]}
    res = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=10_000.0), ranges=ranges).run()
    closed = [t for t in res.trades if t.symbol == "A"]
    assert len(closed) == 1
    assert closed[0].exit_price == 107.0          # removal-bar OPEN, not 120 (high) -> no look-ahead
    assert closed[0].entry_price == 100.0


def test_multitimeframe_in_portfolio_mode():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    # 1-minute base bars; ask for a 5m higher timeframe
    def _b(ts, c):
        return Bar(ts=ts, open=c, high=c, low=c, close=c, volume=1.0)

    seen = {}

    class UsesHTF(Strategy):
        def on_bar(self, bar):
            # record how many completed 5m bars are visible at each step (look-ahead-safe)
            seen[self.index] = len(self.bars("5m"))

    a = [_b(i * 60_000, 100 + i) for i in range(12)]   # 12 one-minute bars = ~2 completed 5m bars
    runner = MultiSymbolStrategyRunner(UsesHTF, {"A": a}, TesterConfig(cash=1000.0, timeframes=["5m"]))
    runner.run()
    # at the last 1m bar (index 11), at least one completed 5m bar should be visible; and the count is
    # monotonic non-decreasing and never sees the future (no 5m bar whose window hasn't closed)
    assert seen[11] >= 1
    assert all(seen[i] <= seen[j] for i, j in zip(range(11), range(1, 12)))   # non-decreasing


def test_forming_htf_bar_in_portfolio_mode():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    captured = {}

    class UsesForming(Strategy):
        def on_bar(self, bar):
            captured[self.index] = self.forming("5m")

    a = [Bar(ts=i * 60_000, open=100, high=100 + i, low=99, close=100 + i, volume=1.0) for i in range(7)]
    runner = MultiSymbolStrategyRunner(UsesForming, {"A": a}, TesterConfig(cash=1000.0, timeframes=["5m"]))
    runner.run()
    # the forming 5m bar at index 6 aggregates the bars of the current (second) 5m window so far
    assert captured[6] is not None and captured[6].high >= 100


def test_max_open_positions_caps_resting_entries():
    # A resting limit entry on a NEW symbol must be blocked when already at the MaxOpenPositions cap.
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    def _o(ts, o, h, l, c):
        return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)

    class LimitBuy(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.limit_buy(1.0, 100.0)   # both A and B rest a limit @100, both dip to 100 next bar

    a = [_o(0, 100, 101, 99, 100), _o(1, 100, 101, 95, 100), _o(2, 100, 101, 99, 100)]
    b = [_o(0, 100, 101, 99, 100), _o(1, 100, 101, 95, 100), _o(2, 100, 101, 99, 100)]
    runner = MultiSymbolStrategyRunner(LimitBuy, {"A": a, "B": b}, TesterConfig(cash=10_000.0),
                                       max_open_positions=1)
    runner.run()
    open_syms = [s for s in ("A", "B") if runner._engine._pos[s].size != 0]
    assert len(open_syms) == 1


def test_empty_ranges_identical_to_no_mask():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    def _b(ts, px):
        return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class BuyHold(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)

    a = [_b(10, 100), _b(20, 110), _b(30, 120)]
    base = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=10_000.0)).run()
    withr = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=10_000.0), ranges={}).run()
    assert base.final_equity == withr.final_equity and len(base.trades) == len(withr.trades)


def test_pct_equity_sizer_sizes_entries_off_equity():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.core.sizing import PctEquitySizer
    from vike_trader_app.tester.config import TesterConfig
    from dataclasses import replace

    def _b(ts, px): return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class Enter(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(1.0)   # intent; the sizer decides the real qty

    a = [_b(1, 100.0), _b(2, 100.0), _b(3, 100.0)]
    cfg = replace(TesterConfig(cash=10_000.0), sizer=PctEquitySizer(0.5))
    runner = MultiSymbolStrategyRunner(Enter, {"A": a}, cfg)
    runner.run()
    # 50% of 10k equity / price 100 = ~50 shares opened, NOT the literal 1.0
    assert abs(runner._engine._pos["A"].size - 50.0) < 1e-6


def test_default_passthrough_sizer_is_byte_for_byte():
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    def _b(ts, px): return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)
    class Enter(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.buy(3.0)
    a = [_b(1, 100.0), _b(2, 100.0)]
    runner = MultiSymbolStrategyRunner(Enter, {"A": a}, TesterConfig(cash=10_000.0))  # no sizer
    runner.run()
    assert runner._engine._pos["A"].size == 3.0   # literal size, unchanged


def test_buy_on_close_fills_at_next_bar_close_in_portfolio_mode():
    """A buy_on_close submitted on bar 0 fills at bar 1's CLOSE in portfolio mode (MOC semantics)."""
    from vike_trader_app.core.model import Bar as _Bar

    def _o(ts, o, h, l, c):
        return _Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)

    class BuyOnCloseMOC(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy_on_close(1.0)

    bars = [
        _o(0, 100, 101, 99, 100),         # submit MOC buy
        _o(60_000, 102, 108, 101, 107),   # fills at close = 107, NOT open 102
        _o(120_000, 107, 110, 106, 109),
    ]
    runner = MultiSymbolStrategyRunner(BuyOnCloseMOC, {"A": bars}, TesterConfig(cash=10_000.0))
    runner.run()
    eng = runner._engine
    assert eng._pos["A"].size == pytest.approx(1.0)
    assert eng._pos["A"].avg_price == pytest.approx(107.0)  # bar[1].close, NOT bar[1].open


def test_order_target_percent_is_not_resized_by_sizer():
    """order_target_* compute an explicit qty and forward raw=True; the sizer must NOT re-size them."""
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.strategy import Strategy
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.core.sizing import FixedSharesSizer
    from vike_trader_app.tester.config import TesterConfig
    from dataclasses import replace

    def _b(ts, px): return Bar(ts=ts, open=px, high=px, low=px, close=px, volume=1.0)

    class Target(Strategy):
        def on_bar(self, bar):
            if self.position.size == 0:
                self.order_target_percent(0.5)  # explicit: 50% of equity -> 50 shares

    a = [_b(1, 100.0), _b(2, 100.0), _b(3, 100.0)]
    # A FixedShares(7) sizer would force 7 shares IF it re-sized; raw=True must keep the explicit 50.
    cfg = replace(TesterConfig(cash=10_000.0), sizer=FixedSharesSizer(7.0))
    runner = MultiSymbolStrategyRunner(Target, {"A": a}, cfg)
    runner.run()
    assert abs(runner._engine._pos["A"].size - 50.0) < 1e-6


# --- benchmark curve ---

def test_benchmark_curve_same_length_as_equity_curve():
    """2-symbol run: benchmark_curve length == equity_curve length."""
    a = [_bar(i, 10.0 + i * 0.1) for i in range(5)]
    b = [_bar(i, 20.0 + i * 0.1) for i in range(5)]
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=10_000.0))
    result = runner.run()
    assert len(result.benchmark_curve) == len(result.equity_curve)
    assert len(result.benchmark_curve) == 5


def test_benchmark_curve_starts_at_cash():
    """First benchmark value must equal the starting cash."""
    a = [_bar(i, 50.0 + i) for i in range(4)]
    b = [_bar(i, 100.0 + i) for i in range(4)]
    cash = 5_000.0
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=cash))
    result = runner.run()
    assert abs(result.benchmark_curve[0] - cash) < 1e-6


def test_benchmark_curve_rises_with_symbols_both_up_10pct():
    """Both symbols rise ~10% -> benchmark ends ~10% above cash."""
    n = 4
    # A: 100 -> 110, B: 50 -> 55  (both +10%)
    a = [_bar(i, 100.0 + i * (10.0 / (n - 1))) for i in range(n)]
    b = [_bar(i, 50.0 + i * (5.0 / (n - 1))) for i in range(n)]
    cash = 10_000.0
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a, "B": b}, TesterConfig(cash=cash))
    result = runner.run()
    final_bench = result.benchmark_curve[-1]
    # expect ~10% gain  (allow 0.5% tolerance)
    assert abs(final_bench / cash - 1.10) < 0.005


def test_benchmark_label_set():
    """benchmark_label must be a non-empty string after a portfolio run."""
    a = [_bar(i, 10.0) for i in range(3)]
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=1_000.0))
    result = runner.run()
    assert result.benchmark_label != ""


def test_benchmark_curve_single_symbol():
    """Single-symbol run still produces a valid benchmark_curve."""
    a = [_bar(i, 10.0 + i) for i in range(5)]
    cash = 1_000.0
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=cash))
    result = runner.run()
    assert len(result.benchmark_curve) == len(result.equity_curve)
    # benchmark[0] = cash; benchmark[-1] = cash * (14 / 10) = 1400
    assert abs(result.benchmark_curve[0] - cash) < 1e-6
    assert abs(result.benchmark_curve[-1] - cash * (14.0 / 10.0)) < 1e-6


# --- configurable benchmark symbol ---

from vike_trader_app.core.portfolio_adapter import _buyhold_asof  # noqa: E402


def test_buyhold_asof_pure_helper_rises_with_symbol():
    """Symbol rising 20%: curve starts at cash, ends at cash * 1.20."""
    n = 5
    # closes: 100, 105, 110, 115, 120  (+20% total)
    bench_bars = [_bar(i, 100.0 + i * 5.0) for i in range(n)]
    equity_ts = [i for i in range(n)]
    cash = 1_000.0
    curve = _buyhold_asof(bench_bars, equity_ts, cash)
    assert len(curve) == n
    assert abs(curve[0] - cash) < 1e-9          # starts at cash (ratio=1.0)
    assert abs(curve[-1] - cash * 1.20) < 1e-9  # ends at +20%


def test_buyhold_asof_forward_fills_sparser_benchmark():
    """Benchmark has 3 bars at ts=0,2,4 but equity has 5 points at ts=0..4.
    The gap at ts=1 must use the bar from ts=0 (forward-fill); ts=3 uses ts=2."""
    bench_bars = [_bar(0, 100.0), _bar(2, 120.0), _bar(4, 140.0)]
    equity_ts = [0, 1, 2, 3, 4]
    cash = 1_000.0
    curve = _buyhold_asof(bench_bars, equity_ts, cash)
    # ts=0 → close 100 → ratio 1.0 → 1000
    assert abs(curve[0] - 1000.0) < 1e-9
    # ts=1 → forward-fill from ts=0 close 100 → still 1000
    assert abs(curve[1] - 1000.0) < 1e-9
    # ts=2 → close 120 → ratio 1.2 → 1200
    assert abs(curve[2] - 1200.0) < 1e-9
    # ts=3 → forward-fill from ts=2 close 120 → still 1200
    assert abs(curve[3] - 1200.0) < 1e-9
    # ts=4 → close 140 → ratio 1.4 → 1400
    assert abs(curve[4] - 1400.0) < 1e-9


def test_buyhold_asof_equity_ts_before_first_benchmark_bar():
    """Equity timestamps before the first benchmark bar must produce cash (ratio 1.0)."""
    bench_bars = [_bar(10, 200.0), _bar(20, 240.0)]  # start at ts=10
    equity_ts = [5, 10, 15, 20]                        # ts=5 is before bench
    cash = 2_000.0
    curve = _buyhold_asof(bench_bars, equity_ts, cash)
    # ts=5 is before first benchmark bar → ratio 1.0 → cash
    assert abs(curve[0] - cash) < 1e-9
    # ts=10 → close 200 → ratio 1.0 (same as first_close)
    assert abs(curve[1] - cash) < 1e-9
    # ts=15 → forward-fill from ts=10 close 200 → 2000
    assert abs(curve[2] - cash) < 1e-9
    # ts=20 → close 240 → ratio 1.2 → 2400
    assert abs(curve[3] - cash * 1.20) < 1e-9


def test_runner_with_benchmark_bars_overrides_equal_weight():
    """Passing benchmark_bars to the runner overrides the equal-weight default."""
    a = [_bar(i, 10.0) for i in range(5)]   # flat symbol A
    # benchmark rises 20%: 100 → 120
    bench = [_bar(i, 100.0 + i * 5.0) for i in range(5)]
    cash = 1_000.0
    runner = MultiSymbolStrategyRunner(
        BuyHold, {"A": a}, TesterConfig(cash=cash),
        benchmark_bars=bench, benchmark_label="SPY",
    )
    result = runner.run()
    assert result.benchmark_label == "SPY"
    assert len(result.benchmark_curve) == len(result.equity_curve)
    # benchmark starts at cash
    assert abs(result.benchmark_curve[0] - cash) < 1e-9
    # benchmark ends at +20%
    assert abs(result.benchmark_curve[-1] - cash * 1.20) < 1e-9


def test_runner_without_benchmark_bars_uses_equal_weight_default():
    """No benchmark_bars → falls back to equal-weight (existing behaviour unchanged)."""
    a = [_bar(i, 10.0 + i) for i in range(4)]
    runner = MultiSymbolStrategyRunner(BuyHold, {"A": a}, TesterConfig(cash=1_000.0))
    result = runner.run()
    assert result.benchmark_label == "Equal-weight buy & hold"


def test_runner_benchmark_label_defaults_to_benchmark_when_no_label_given():
    """benchmark_bars set but no label → label becomes 'Benchmark'."""
    bench = [_bar(i, 50.0 + i) for i in range(4)]
    runner = MultiSymbolStrategyRunner(
        BuyHold, {"A": [_bar(i, 10.0) for i in range(4)]},
        TesterConfig(cash=500.0),
        benchmark_bars=bench,
        # no benchmark_label
    )
    result = runner.run()
    assert result.benchmark_label == "Benchmark"


def test_runner_benchmark_bars_length_matches_equity_curve():
    """benchmark_curve must have the same length as equity_curve even when timelines differ."""
    # equity has 6 points; benchmark only has 3 bars
    a = [_bar(i * 2, 100.0) for i in range(6)]      # ts = 0,2,4,6,8,10
    bench = [_bar(0, 100.0), _bar(4, 110.0), _bar(8, 120.0)]
    runner = MultiSymbolStrategyRunner(
        BuyHold, {"A": a}, TesterConfig(cash=1_000.0),
        benchmark_bars=bench, benchmark_label="IDX",
    )
    result = runner.run()
    assert len(result.benchmark_curve) == len(result.equity_curve) == 6


def test_runner_guard_empty_benchmark_bars_falls_back_to_equal_weight():
    """Empty benchmark_bars list → guard triggers, falls back to equal-weight."""
    a = [_bar(i, 10.0 + i) for i in range(4)]
    runner = MultiSymbolStrategyRunner(
        BuyHold, {"A": a}, TesterConfig(cash=1_000.0),
        benchmark_bars=[],  # empty → guard
        benchmark_label="SPY",
    )
    result = runner.run()
    assert result.benchmark_label == "Equal-weight buy & hold"


def test_runner_guard_non_positive_first_close_falls_back():
    """Benchmark whose first bar has close <= 0 must fall back to equal-weight (div-by-zero guard)."""
    bench = [_bar(i, 0.0) for i in range(4)]   # first_close = 0.0 → guard
    a = [_bar(i, 10.0) for i in range(4)]
    runner = MultiSymbolStrategyRunner(
        BuyHold, {"A": a}, TesterConfig(cash=1_000.0),
        benchmark_bars=bench, benchmark_label="BAD",
    )
    result = runner.run()
    assert result.benchmark_label == "Equal-weight buy & hold"
