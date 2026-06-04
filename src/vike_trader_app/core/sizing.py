"""Swappable position sizers (WealthLab's PosSizer model).

A PositionSizer turns a strategy's entry *intent* into an actual quantity using live portfolio
context, so the SAME strategy can be run under fixed-dollar / %-equity / fixed-shares sizing without
editing it. The default PassThrough returns the strategy's literal size (today's behavior)."""

from dataclasses import dataclass


@dataclass
class SizeContext:
    symbol: str
    side: int                # +1 buy / -1 sell (opening direction)
    intent: float            # the raw size the strategy passed (used only by PassThrough)
    basis_price: float       # entry basis (current close)
    equity: float
    cash: float
    multiplier: float


class PositionSizer:
    """Return the quantity (>= 0) to open for this entry intent. Override `size`."""
    def size(self, ctx: "SizeContext") -> float:
        raise NotImplementedError


class PassThroughSizer(PositionSizer):
    """Default: the strategy's own size, unchanged."""
    def size(self, ctx):
        return ctx.intent


class FixedDollarSizer(PositionSizer):
    """Each entry is a fixed cash notional."""
    def __init__(self, amount: float):
        self.amount = amount

    def size(self, ctx):
        denom = ctx.basis_price * ctx.multiplier
        return self.amount / denom if denom else 0.0


class FixedSharesSizer(PositionSizer):
    def __init__(self, shares: float):
        self.shares = shares

    def size(self, ctx):
        return self.shares


class PctEquitySizer(PositionSizer):
    """Each entry targets `pct` of current account equity."""
    def __init__(self, pct: float):
        self.pct = pct

    def size(self, ctx):
        denom = ctx.basis_price * ctx.multiplier
        return (self.pct * ctx.equity) / denom if denom else 0.0
