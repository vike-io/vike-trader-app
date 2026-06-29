"""S5 — decomposed Account at the single-backtest REPORTING boundary.

Three guarantees:
  1. SimulatedExchange(sim_account=acc) subscribes the Account on the bus so a real fill folds.
  2. The single REPORT run (StrategyTester.run / Backtester(..., mirror=True)) builds a mirror
     Account whose equity == engine.final_equity (abs < 1e-10).
  3. DETERMINISTIC perf gate: the OPTIMIZER path (engine via config.engine_kwargs(),
     SimulatedExchange without sim_account) constructs NO Account and NO hooks.
"""

from __future__ import annotations

import pytest

from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.exec.accounting import Account
from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.events import FillEvent, FundingEvent
from vike_trader_app.exec.sim_exchange import SimulatedExchange
from vike_trader_app.tester.backtester import Backtester
from vike_trader_app.tester.config import TesterConfig
from vike_trader_app.tester.strategy_tester import StrategyTester

TOL = 1e-10


def _bar(ts, o, c):
    return Bar(ts=ts, open=o, high=max(o, c) + 5, low=min(o, c) - 5, close=c, volume=1.0)


def _ramp():
    closes = [100, 110, 120, 130, 120, 110, 100, 110]
    return [_bar(i * 60_000, c, c) for i, c in enumerate(closes)]


class _BuyThenClose(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 3:
            self.close()


def test_sim_exchange_subscribes_mirror_account():
    bus = EventBus()
    acc = Account(multiplier=1.0)
    eng = SingleSymbolEngine(_ramp(), _BuyThenClose(), cash=10_000.0, taker_fee=0.001)
    sx = SimulatedExchange(eng, bus, venue="sim", symbol="X", sim_account=acc)
    assert sx.sim_account is acc
    eng.run()
    assert len(acc.trades) == 1
    assert acc.positions[("sim", "X", "BOTH")]["size"] == pytest.approx(0.0, abs=TOL)
    acc.set_mark("sim", "X", _ramp()[-1].close)
    assert acc.equity(10_000.0, venue="sim", symbol="X") == pytest.approx(eng.equity_now(), abs=TOL)


def test_report_run_mirror_equity_matches_engine():
    cfg = TesterConfig(cash=10_000.0, taker_fee=0.001)
    bt = Backtester(_BuyThenClose(), _ramp(), cfg, mirror=True)
    report = bt.run()
    acc = bt.sim_account
    assert acc is not None, "mirror=True must attach a decomposed Account"
    acc.set_mark("sim", "X", _ramp()[-1].close)
    assert acc.equity(cfg.cash, venue="sim", symbol="X") == pytest.approx(report.final_equity, abs=TOL)
    assert acc.trades
    assert acc.fees_paid > 0.0


def test_report_run_default_attaches_no_mirror():
    cfg = TesterConfig(cash=10_000.0, taker_fee=0.001)
    bt = Backtester(_BuyThenClose(), _ramp(), cfg)  # mirror defaults False
    bt.run()
    assert bt.sim_account is None


class _NoTradeStrategy(Strategy):
    def on_bar(self, bar):
        pass


def test_strategy_tester_run_uses_mirror_path():
    cfg = TesterConfig(cash=10_000.0, taker_fee=0.001)
    st = StrategyTester(_BuyThenClose(), _ramp(), cfg)
    captured: dict = {}
    real_run = Backtester.run

    def _spy_run(self):
        captured["mirror"] = self.mirror
        rep = real_run(self)
        captured["sim_account"] = self.sim_account
        return rep

    Backtester.run = _spy_run
    try:
        report = st.run()
    finally:
        Backtester.run = real_run
    assert captured["mirror"] is True
    assert captured["sim_account"] is not None
    bare = SingleSymbolEngine(_ramp(), _BuyThenClose(), **cfg.engine_kwargs()).run()
    assert report.final_equity == pytest.approx(bare.final_equity, abs=TOL)


def test_optimizer_trial_builds_bare_backtester(monkeypatch):
    seen: list[bool] = []
    real_init = Backtester.__init__

    def _spy(self, strategy, bars, config=None, *, mirror=False):
        seen.append(mirror)
        real_init(self, strategy, bars, config, mirror=mirror)

    monkeypatch.setattr(Backtester, "__init__", _spy)
    cfg = TesterConfig(cash=10_000.0)
    st = StrategyTester(_NoTradeStrategy(), _ramp(), cfg)
    st.optimize(lambda **_kw: _NoTradeStrategy(), {"_": [0]}, criterion="sharpe", method="grid")
    assert seen
    assert all(m is False for m in seen), f"optimizer built a mirror Backtester: {seen}"


def test_deterministic_sweep_builds_no_account_or_hooks():
    cfg = TesterConfig(cash=10_000.0, taker_fee=0.001, multiplier=5.0)
    eng = SingleSymbolEngine(_ramp(), _BuyThenClose(), **cfg.engine_kwargs())
    assert eng._on_fill is None
    assert eng._on_submit is None
    assert eng._on_funding is None
    kw = cfg.engine_kwargs()
    for forbidden in ("on_fill", "on_submit", "on_funding", "sim_account"):
        assert forbidden not in kw, f"engine_kwargs() leaked {forbidden!r}"
    pkw = cfg.portfolio_engine_kwargs()
    for forbidden in ("on_fill", "on_submit", "on_funding", "sim_account"):
        assert forbidden not in pkw, f"portfolio_engine_kwargs() leaked {forbidden!r}"
    bus = EventBus()
    sx = SimulatedExchange(eng, bus, venue="sim", symbol="X")
    assert sx.sim_account is None
    bt = Backtester(_BuyThenClose(), _ramp(), cfg)
    bt.run()
    assert bt.sim_account is None
