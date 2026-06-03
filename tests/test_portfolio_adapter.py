# tests/test_portfolio_adapter.py
"""WealthLab-style portfolio backtest: one single-symbol Strategy per symbol, shared cash."""

from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio_adapter import align_bars


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


from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy
from vike_trader_app.core.portfolio_adapter import SymbolEngineShim


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


def test_shim_resting_orders_raise_in_portfolio_mode():
    import pytest
    eng = PortfolioEngine({"A": [_bar(1, 1.0)]}, PortfolioStrategy(), cash=10.0)
    shim = SymbolEngineShim(eng, "A", None)
    with pytest.raises(NotImplementedError):
        shim.submit_limit(+1, 1.0, 0.5)
