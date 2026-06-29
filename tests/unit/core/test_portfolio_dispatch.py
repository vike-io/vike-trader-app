# tests/unit/core/test_portfolio_dispatch.py
"""TDD: Task 3 — MultiSymbolEngine dispatches via strategy._on_step seam."""
from vike_trader_app.core.multi_symbol_engine import PortfolioStrategy, MultiSymbolEngine
from vike_trader_app.core.model import Bar


def _series(n):
    return [Bar(ts=i * 60000, open=1, high=1, low=1, close=1) for i in range(n)]


def test_engine_calls_on_step_hook():
    """Engine must call strategy._on_step(ts, bars) once per bar."""
    seen = []

    class S(PortfolioStrategy):
        def _on_step(self, ts, bars):
            seen.append((ts, set(bars)))

    MultiSymbolEngine({"BTC": _series(3), "ETH": _series(3)}, S(), fee_rate=0.0, cash=1000).run()
    assert len(seen) == 3 and seen[0][1] == {"BTC", "ETH"}


def test_default_on_step_calls_on_bar_bundle():
    """Default _on_step must route to on_bar(ts, bars) — legacy bundle behavior unchanged."""
    calls = []

    class S(PortfolioStrategy):
        def on_bar(self, ts, bars):
            calls.append(ts)

    MultiSymbolEngine({"BTC": _series(2)}, S(), fee_rate=0.0, cash=1000).run()
    assert len(calls) == 2  # default _on_step still routes to on_bar(ts, bars)


def test_bars_pretagged_with_symbol():
    """MultiSymbolEngine pre-tags each bar with its SYMBOL.VENUE id at construction."""
    eng = MultiSymbolEngine({"BTC": _series(2)}, PortfolioStrategy(), fee_rate=0.0, cash=1000,
                          default_venue="binance")
    assert eng.bars["BTC"][0].symbol == "BTC.BINANCE"
