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
                      root: str, kind: str = "quotes", per_tick: bool = False,
                      **engine_kwargs) -> Result:
    if kind == "quotes":
        ticks = tick_store.read_quotes(root, symbol, start_ms, end_ms)
        if not ticks:
            raise NoTickData(f"no quote-tick data for {symbol} [{start_ms}..{end_ms}]")
    elif kind == "trades":
        ticks = tick_store.read_trades(root, symbol, start_ms, end_ms)
        if not ticks:
            raise NoTickData(f"no trade-tick data for {symbol} [{start_ms}..{end_ms}]")
    else:
        raise ValueError(f"unknown tick kind {kind!r} (expected 'quotes' or 'trades')")
    if per_tick:
        # per-tick engine: strategy uses on_quote_tick/on_trade_tick; fills resolve per tick.
        engine = BacktestEngine([], strategy, fill_model=TickFillModel(), **engine_kwargs)
        return engine.run_ticks(ticks)
    # Slice-1 path: consolidate to bars, run the bar loop with spread-crossing fills.
    step_ms = parse_timeframe(interval)
    bars = consolidate_quotes(ticks, step_ms) if kind == "quotes" else consolidate_trades(ticks, step_ms)
    engine = BacktestEngine(bars, strategy, fill_model=TickFillModel(), **engine_kwargs)
    return engine.run()
