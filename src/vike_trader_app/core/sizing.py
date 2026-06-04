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
    atr: float = 0.0         # recent average true range (0 = unavailable)
    drawdown: float = 0.0    # current account drawdown fraction 0..1 (0 = no drawdown)
    risk_stop: float | None = None  # protective stop price for this entry (risk sizers need it)
    open_risk: float = 0.0   # sum of $ risk already open across the book (for portfolio-heat caps)


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


class PctVolatilitySizer(PositionSizer):
    """Size so the position's ATR-risk is ``pct`` of equity.

    qty = pct * equity / (ATR * multiplier).  Falls back to a %-equity-style
    size (using basis_price instead of ATR) when ATR is unavailable (0).
    """

    def __init__(self, pct: float):
        self.pct = pct

    def size(self, ctx: "SizeContext") -> float:
        if ctx.atr and ctx.multiplier:
            return (self.pct * ctx.equity) / (ctx.atr * ctx.multiplier)
        denom = ctx.basis_price * ctx.multiplier
        return (self.pct * ctx.equity) / denom if denom else 0.0


class MaxRiskPctSizer(PositionSizer):
    """Size so the trade risks ``pct`` of equity given the stop distance:
    ``qty = pct*equity / (|basis-stop|*mult)``.

    Returns 0 if there is no ``risk_stop`` or the stop distance is zero/inverted (risk sizing
    fundamentally requires a stop to define risk-per-unit).
    """

    def __init__(self, pct: float):
        self.pct = pct

    def size(self, ctx: "SizeContext") -> float:
        if ctx.risk_stop is None:
            return 0.0
        risk_per_unit = abs(ctx.basis_price - ctx.risk_stop) * ctx.multiplier
        return (self.pct * ctx.equity) / risk_per_unit if risk_per_unit > 0 else 0.0


class PortfolioHeatSizer(PositionSizer):
    """Wrap a base sizer; cap TOTAL open risk to ``max_heat`` * equity.

    Reduces the base qty so ``open_risk + this_trade_risk <= max_heat*equity``. Needs a
    ``risk_stop`` to know this trade's risk-per-unit; if absent, passes the base qty through.
    """

    def __init__(self, base: "PositionSizer", max_heat: float):
        self.base, self.max_heat = base, max_heat

    def size(self, ctx: "SizeContext") -> float:
        qty = self.base.size(ctx)
        if ctx.risk_stop is None:
            return qty
        risk_per_unit = abs(ctx.basis_price - ctx.risk_stop) * ctx.multiplier
        if risk_per_unit <= 0:
            return qty
        budget = self.max_heat * ctx.equity - ctx.open_risk
        if budget <= 0:
            return 0.0
        max_qty = budget / risk_per_unit
        return min(qty, max_qty)


class DrawdownThrottleSizer(PositionSizer):
    """Wrap a base sizer and scale its size down as account drawdown deepens.

    factor = max(floor, 1 - sensitivity * drawdown).
    Lets you de-risk automatically in drawdown regimes.
    """

    def __init__(self, base: "PositionSizer", sensitivity: float = 1.0, floor: float = 0.0):
        self.base = base
        self.sensitivity = sensitivity
        self.floor = floor

    def size(self, ctx: "SizeContext") -> float:
        factor = max(self.floor, 1.0 - self.sensitivity * ctx.drawdown)
        return self.base.size(ctx) * factor
