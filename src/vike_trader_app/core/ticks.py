"""Tick value types: L1 quote ticks (bid/ask) and trade ticks (the tape)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteTick:
    """One L1 quote update: best bid/ask (forex via Dukascopy, crypto bookTicker)."""

    ts: int  # epoch milliseconds (UTC)
    bid: float
    ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    symbol: str = ""  # instrument id — empty for single-symbol paths (additive/backward-compat)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass(frozen=True)
class TradeTick:
    """One executed trade: price + size, with the aggressor flag where available."""

    ts: int  # epoch milliseconds (UTC)
    price: float
    size: float
    is_buyer_maker: bool = False
    symbol: str = ""  # instrument id — empty for single-symbol paths (additive/backward-compat)
