"""Strict tick-mode backtest entry point.

Loads ticks from the store, consolidates them to ephemeral bars (quotes carry bid/ask),
and runs the engine with the spread-crossing ``TickFillModel``. STRICT: if the requested
symbol/period has no tick data it raises ``NoTickData`` rather than silently falling back
to bar fills — a tick run is genuinely tick-filled or it fails loudly.
"""

from .consolidator import consolidate_quotes, consolidate_trades
from .engine import BacktestEngine, Result
from .fill_model import TickFillModel
from .timeframe import parse_timeframe
from ..data import tick_store


class NoTickData(Exception):
    """Raised when a tick backtest is requested but no tick data exists for it."""


def run_tick_backtest(strategy, *, symbol: str, interval: str, start_ms: int, end_ms: int,
                      root: str, kind: str = "quotes", **engine_kwargs) -> Result:
    step_ms = parse_timeframe(interval)
    if kind == "quotes":
        ticks = tick_store.read_quotes(root, symbol, start_ms, end_ms)
        if not ticks:
            raise NoTickData(f"no quote-tick data for {symbol} [{start_ms}..{end_ms}]")
        bars = consolidate_quotes(ticks, step_ms)
    elif kind == "trades":
        ticks = tick_store.read_trades(root, symbol, start_ms, end_ms)
        if not ticks:
            raise NoTickData(f"no trade-tick data for {symbol} [{start_ms}..{end_ms}]")
        bars = consolidate_trades(ticks, step_ms)
    else:
        raise ValueError(f"unknown tick kind {kind!r} (expected 'quotes' or 'trades')")
    engine = BacktestEngine(bars, strategy, fill_model=TickFillModel(), **engine_kwargs)
    return engine.run()
