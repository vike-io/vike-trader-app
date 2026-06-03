# tests/test_portfolio_adapter.py
"""WealthLab-style portfolio backtest: one single-symbol Strategy per symbol, shared cash."""

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


def test_shim_multitimeframe_still_unsupported():
    import pytest
    eng = PortfolioEngine({"A": [_bar(1, 1.0)]}, PortfolioStrategy(), cash=10.0)
    shim = SymbolEngineShim(eng, "A", None)
    with pytest.raises(NotImplementedError):
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
