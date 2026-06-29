"""Tests for the SingleSymbolStrategy compat shim (Task 2).

Confirms:
- Strategy (core.strategy) is the NEW unified class (Task 6), NOT the compat alias
- The full single-symbol API surface is intact on SingleSymbolStrategy
- position is still a property
- Subclassing SingleSymbolStrategy emits DeprecationWarning (Task 9)
- A SingleSymbolStrategy subclass runs via MultiSymbolStrategyRunner at N=1 (Task 9)
"""
import inspect
import warnings

import pytest

from vike_trader_app.core.compat_strategy import SingleSymbolStrategy
from vike_trader_app.core.strategy import Strategy


def test_strategy_is_no_longer_single_symbol_alias():
    """Task 6: Strategy is now the unified per-symbol class, not the compat shim."""
    assert Strategy is not SingleSymbolStrategy
    # The new Strategy has a per-symbol on_bar(self, bar) — one positional arg
    sig = inspect.signature(Strategy.on_bar)
    params = list(sig.parameters)
    assert params == ["self", "bar"], f"unexpected on_bar signature: {params}"
    # Reserved P2/P3 tick handlers exist on the new class
    assert hasattr(Strategy, "on_quote_tick")
    assert hasattr(Strategy, "on_trade_tick")
    assert hasattr(Strategy, "on_order_book")


def test_single_symbol_api_intact():
    assert hasattr(SingleSymbolStrategy, "on_bar")
    assert callable(SingleSymbolStrategy.buy)        # old no-symbol verb still present
    assert isinstance(SingleSymbolStrategy.position, property)   # old property-style read


def test_full_verb_surface():
    """Confirm the complete verb/lifecycle surface is preserved."""
    for name in [
        "sell", "close",
        "limit_buy", "limit_sell", "stop_buy", "stop_sell",
        "trailing_stop", "trailing_stop_cover",
        "buy_on_close", "sell_on_close",
        "limit_buy_on_close", "limit_sell_on_close",
        "cancel_all",
        "order_target_shares", "order_target_value", "order_target_percent",
        "on_bar", "on_quote_tick", "on_trade_tick",
        "on_start", "on_stop",
        "on_order_submitted", "on_order_accepted", "on_order_rejected",
        "on_order_filled", "on_order_canceled", "on_order_expired", "on_order_updated",
        "on_position_opened", "on_position_changed", "on_position_closed",
        "on_event", "on_liquidation",
        "chart_overlays",
        "history", "history_async", "bars", "forming",
        "make",
    ]:
        assert hasattr(SingleSymbolStrategy, name), f"missing: {name}"


def test_properties_intact():
    """equity, drawdown, now are also properties/attrs on the class."""
    assert isinstance(SingleSymbolStrategy.equity, property)
    assert isinstance(SingleSymbolStrategy.drawdown, property)
    assert isinstance(SingleSymbolStrategy.now, property)


def test_class_attributes():
    """PARAM_GRID, WARMUP class-level attributes preserved."""
    assert hasattr(SingleSymbolStrategy, "PARAM_GRID")
    assert hasattr(SingleSymbolStrategy, "WARMUP")
    assert SingleSymbolStrategy.WARMUP == 0
    assert isinstance(SingleSymbolStrategy.PARAM_GRID, dict)


# --- Task 9 tests ---

def test_subclassing_warns():
    """Subclassing SingleSymbolStrategy emits DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="SingleSymbolStrategy.*deprecated"):
        class Old(SingleSymbolStrategy):
            def on_bar(self, bar):
                if self.index == 0:
                    self.buy(1.0)


def test_old_strategy_runs_via_shim_at_n1():
    """A SingleSymbolStrategy subclass runs via MultiSymbolStrategyRunner at N=1."""
    from vike_trader_app.core.model import Bar
    from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
    from vike_trader_app.tester.config import TesterConfig

    # Define the subclass, suppressing the DeprecationWarning so it doesn't fail the run
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class OldStyleStrategy(SingleSymbolStrategy):
            def on_bar(self, bar):
                if self.index == 0:
                    self.buy(1.0)

    bars = [Bar(ts=i * 60_000, open=10.0, high=11.0, low=9.0, close=10.0) for i in range(5)]
    config = TesterConfig(cash=1000.0, fee_rate=0.0)

    runner = MultiSymbolStrategyRunner(OldStyleStrategy, {"BTC": bars}, config)
    result = runner.run()

    assert result.final_equity is not None
    assert isinstance(result.final_equity, float)
