"""Tests for the SingleSymbolStrategy compat shim (Task 2).

Confirms:
- Strategy (core.strategy) is the same object as SingleSymbolStrategy (core.compat_strategy)
- The full single-symbol API surface is intact on SingleSymbolStrategy
- position is still a property
"""
import warnings
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy
from vike_trader_app.core.strategy import Strategy   # temporary alias this phase


def test_alias_points_at_compat_this_phase():
    assert Strategy is SingleSymbolStrategy


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
