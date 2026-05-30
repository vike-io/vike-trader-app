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


@dataclass
class Position:
    """A net position in one instrument."""

    size: float = 0.0
    avg_price: float = 0.0

    def unrealized_pnl(self, price: float) -> float:
        """Mark-to-market PnL at ``price`` (handles long and short via the signed size)."""
        return (price - self.avg_price) * self.size


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
