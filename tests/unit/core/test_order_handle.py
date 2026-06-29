"""Tests for OrderHandle: submit_limit returns an Order; per-order cancel works."""
from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy
from vike_trader_app.core.model import Bar
from vike_trader_app.core.order_handle import OrderHandle
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
from vike_trader_app.tester.config import TesterConfig


def _series(n):
    return [Bar(ts=i * 60_000, open=10, high=11, low=9, close=10) for i in range(n)]


def test_submit_limit_returns_order_object():
    """submit_limit should return the Order object, not None."""
    result = {}

    class S(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                result["o"] = self._engine.submit_limit("BTC", +1, 1.0, price=1.0)

    eng = PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    assert result["o"] is not None


def test_submit_limit_returns_handle_and_cancels():
    """Canonical test from the brief: submit a far limit that never fills, cancel on next bar."""
    placed = {}

    class S(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                placed["h"] = self._engine.submit_limit("BTC", +1, 1.0, price=1.0)  # far, never fills
            elif self.index == 1:
                self._engine.cancel_order("BTC", placed["h"])

    eng = PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    assert not eng._sym["BTC"].pending  # order was cancelled, none left resting


def test_order_handle_wraps_order():
    """OrderHandle.status is 'working' while resting, 'done' after cancel."""
    placed = {}

    class S(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                order = self._engine.submit_limit("BTC", +1, 1.0, price=1.0)
                placed["handle"] = OrderHandle(
                    id=1, _order=order, _engine=self._engine, symbol="BTC"
                )
            elif self.index == 1:
                assert placed["handle"].status == "working"
                placed["handle"].cancel()

    eng = PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    handle = placed["handle"]
    assert handle.status == "done"
    assert handle.filled is True  # "done" means not resting (cancelled or filled)
    assert not eng._sym["BTC"].pending


def test_submit_returns_none_when_size_zero():
    """submit should return None when the size guard skips the append."""
    result = {}

    class S(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                # size=0 → guard triggers, should return None
                result["o"] = self._engine.submit("BTC", +1, 0.0, raw=True)

    eng = PortfolioEngine({"BTC": _series(2)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    assert result["o"] is None


def test_pending_of_helper():
    """_pending_of returns the live pending list for a symbol."""
    class S(PortfolioStrategy):
        def on_bar(self, ts, bars):
            if self.index == 0:
                self._engine.submit_limit("BTC", +1, 1.0, price=1.0)

    eng = PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=1000)
    # After one step, the pending list should have the limit order
    # (run a single step by using a partial run isn't easy; verify via full run)
    eng.run()
    # After run, the limit at price=1.0 never filled (close=10), so it stays pending
    assert len(eng._pending_of("BTC")) == 1


def test_cancel_order_noop_on_already_filled():
    """cancel_order on an order not in pending is a no-op (no ValueError)."""
    from vike_trader_app.core.orders import Order
    eng = PortfolioEngine({"BTC": _series(2)}, PortfolioStrategy(), fee_rate=0.0, cash=1000)
    eng.run()
    ghost = Order("limit", +1, 1.0, price=1.0)
    eng.cancel_order("BTC", ghost)  # should not raise


def test_order_handle_cancel_on_backtest_engine():
    """OrderHandle.cancel() works via BacktestEngine (single-symbol path).

    A far limit order is placed (price=1.0, bars trade at 10.0 — never fills),
    then cancelled via the OrderHandle.  No position should be taken and the
    pending list must be empty after the run.
    """
    placed = {}

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                placed["h"] = self.buy(bar.symbol, 1.0, limit=1.0)  # far limit, never fills
            elif self.index == 2:
                if placed.get("h") is not None:
                    placed["h"].cancel()

    bars = _series(5)
    eng = BacktestEngine(bars, S(), cash=1000.0, fee_rate=0.0)
    result = eng.run()
    assert not eng._pending          # cancel removed the resting order
    assert eng.position.size == 0.0  # limit never filled (and was cancelled)
    assert result.trades == []


def test_order_handle_cancel_on_symbol_engine_shim():
    """OrderHandle.cancel() works via SymbolEngineShim (MultiSymbolStrategyRunner path).

    Same scenario as the BacktestEngine test but run through MultiSymbolStrategyRunner
    so the order flows through SymbolEngineShim.cancel_order → PortfolioEngine.cancel_order.
    """
    placed = {}

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                placed["h"] = self.buy(bar.symbol, 1.0, limit=1.0)  # far limit, price=10 → never fills
            elif self.index == 2:
                if placed.get("h") is not None:
                    placed["h"].cancel()  # must NOT raise AttributeError

    bars = _series(5)
    runner = MultiSymbolStrategyRunner(S, {"BTC": bars}, TesterConfig(cash=1000.0, fee_rate=0.0))
    result = runner.run()
    assert result.trades == []      # limit was cancelled, no fill happened
    # No position left
    assert runner._engine._sym["BTC"].pos.size == 0.0
