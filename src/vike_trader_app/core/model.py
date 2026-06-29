"""Domain model: the core value objects shared across the engine.

`Position` uses a SIGNED size: > 0 long, < 0 short, 0 flat.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle. Resolution-agnostic: a bar is just a timestamped price event."""

    ts: int  # epoch milliseconds (UTC)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    funding: float | None = None  # perp funding rate at this bar, if available
    bid: float | None = None  # opening best-bid for the bar's window (tick-derived; None for OHLCV bars)
    ask: float | None = None  # opening best-ask for the bar's window (tick-derived; None for OHLCV bars)
    symbol: str | None = None  # fully-qualified "SYMBOL.VENUE" id, attached by the engine dispatch


@dataclass
class Position:
    """A net position in one instrument."""

    size: float = 0.0
    avg_price: float = 0.0

    def unrealized_pnl(self, price: float) -> float:
        """Mark-to-market PnL at ``price`` (handles long and short via the signed size)."""
        return (price - self.avg_price) * self.size


@dataclass(frozen=True)
class Fill:
    """One execution delivered to ``Strategy.on_order_filled`` (and ``on_liquidation`` for a forced close)."""

    side: int        # +1 buy / -1 sell
    size: float
    price: float     # fill price after slippage
    fee: float
    ts: int          # epoch milliseconds (UTC)
    is_maker: bool = False
    symbol: str = ""


@dataclass
class Trade:
    """A completed round-trip. ``pnl`` is gross price PnL; ``fees`` is the round-trip cost."""

    entry_price: float
    exit_price: float
    size: float
    pnl: float
    fees: float = 0.0
    entry_ts: int = 0  # fill timestamp of the opening order (epoch ms)
    exit_ts: int = 0  # fill timestamp of the closing order (epoch ms)
    symbol: str = ""  # originating symbol ("" for single-symbol engine)
    # MAE/MFE as fractions relative to entry price (portfolio mode only; 0.0 = not tracked).
    # mae: max adverse excursion (negative = adverse for long, positive = adverse for short) expressed
    #      as (low - entry)/entry for longs, (entry - high)/entry for shorts.
    # mfe: max favorable excursion expressed as (high - entry)/entry for longs, (entry - low)/entry for shorts.
    mae: float = 0.0
    mfe: float = 0.0
