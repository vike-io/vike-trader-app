"""StrategyEngine Protocol: BacktestEngine and SymbolEngineShim must expose the same Strategy-facing
surface, so a single-symbol Strategy runs identically standalone and in a portfolio. Catches drift."""

from vike_trader_app.core.engine import BacktestEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.portfolio import PortfolioEngine, PortfolioStrategy
from vike_trader_app.core.portfolio_adapter import SymbolEngineShim
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.strategy_engine import StrategyEngine

_METHODS = (
    "position", "equity_now", "drawdown_now", "submit", "submit_close", "submit_limit",
    "submit_stop", "submit_trailing", "submit_market_close", "submit_limit_close", "cancel_all",
    "order_target", "order_target_value", "order_target_percent", "bars_for", "forming_for",
)


def _bar(ts=0):
    return Bar(ts=ts, open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0)


def test_backtest_engine_satisfies_protocol():
    eng = BacktestEngine([_bar()], Strategy())
    assert isinstance(eng, StrategyEngine)


def test_symbol_engine_shim_satisfies_protocol():
    pf = PortfolioEngine({"X": [_bar()]}, PortfolioStrategy())
    shim = SymbolEngineShim(pf, "X", None)
    assert isinstance(shim, StrategyEngine)


def test_protocol_names_every_method_both_classes_expose():
    eng = BacktestEngine([_bar()], Strategy())
    pf = PortfolioEngine({"X": [_bar()]}, PortfolioStrategy())
    shim = SymbolEngineShim(pf, "X", None)
    for name in _METHODS:
        assert hasattr(eng, name), f"BacktestEngine missing {name}"
        assert hasattr(shim, name), f"SymbolEngineShim missing {name}"
