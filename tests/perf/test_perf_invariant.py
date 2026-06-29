"""DETERMINISTIC perf gate (S6): the optimizer's hot-path engine must carry NO ledger mirror.

The sweep builds its engine via engine_kwargs() (single) / portfolio_engine_kwargs() (portfolio),
neither of which passes on_fill -> _on_fill/_on_submit/_on_funding all None, NO Account per combo ->
scalar hot path byte-identical to pre-S1. Asserted directly (no timing, no flakiness).
"""

from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.tester.config import TesterConfig


class _BuyHold(Strategy):
    def on_bar(self, bar):  # noqa: ARG002
        if self.index == 0:
            self.buy(1.0)


def _bars(n=8):
    return [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(n)]


def test_single_symbol_sweep_engine_carries_no_ledger_mirror():
    cfg = TesterConfig(taker_fee=0.001, cash=10_000.0, multiplier=2.0)
    kw = cfg.engine_kwargs()
    assert "on_fill" not in kw and "sim_account" not in kw and "on_funding" not in kw
    eng = SingleSymbolEngine(_bars(), _BuyHold(), **kw)
    assert eng._on_fill is None
    assert eng._on_submit is None
    assert eng._on_funding is None


def test_portfolio_sweep_engine_carries_no_ledger_mirror():
    cfg = TesterConfig(taker_fee=0.001, cash=10_000.0, multiplier=2.0)
    kw = cfg.portfolio_engine_kwargs()
    assert "on_fill" not in kw and "sim_account" not in kw and "on_funding" not in kw
    eng = MultiSymbolEngine({"A": _bars(), "B": _bars()}, _BuyHold(), **kw)
    assert eng._on_fill is None
    assert eng._on_submit is None
    assert eng._on_funding is None
