"""Pure column model for the options-chain grid (no Qt).

Defines the per-side field set for the two views — "chain" (TradingView/TradeStation style:
LTP/Theor/Spread/Bid%/Ask%/Distance/Rel dist/Ann%/Volume) and "greeks" (Δ/Γ/Θ/V) — plus the
value + display computation for each field. The UI consumes these to build cells, so the
math/formatting is unit-tested without a widget.
"""

from __future__ import annotations

from .greeks import black_scholes_price
from .model import OptionQuote

# Per-side field order, CENTRE -> OUTER (i.e. the order on the puts side, left to right).
# The calls side uses the reverse so the table mirrors around the central Strike/IV.
# NB: "annbid"/"annask" (annualized yield) and "ltp" (last traded price) are intentionally
# omitted from the displayed chain — their value/format logic is kept below (still unit-tested)
# so the columns can be re-enabled by adding them back here.
CHAIN_FIELDS = ["volume", "distance", "reldist", "bid", "ask", "spread",
                "theor", "bidpct", "askpct"]
GREEKS_FIELDS = ["volume", "oi", "bid", "ask", "mark", "delta", "gamma", "theta", "vega"]

HEADERS = {
    "volume": "Volume", "distance": "Distance", "reldist": "Rel dist", "bid": "Bid", "ask": "Ask",
    "spread": "Spread", "theor": "Theor", "ltp": "LTP", "bidpct": "Bid %", "askpct": "Ask %",
    "annbid": "Ann bid %", "annask": "Ann ask %", "oi": "OI", "mark": "Mark", "iv": "IV",
    "delta": "Δ", "gamma": "Γ", "theta": "Θ", "vega": "V",
}

# value kind -> formatting; "bar" renders a magnitude bar behind an integer (volume).
_KIND = {
    "volume": "bar", "oi": "int", "distance": "px", "reldist": "pct", "bid": "px", "ask": "px",
    "spread": "pct", "theor": "px", "ltp": "px", "bidpct": "pct", "askpct": "pct",
    "annbid": "pct", "annask": "pct",
    "mark": "px", "iv": "pct", "delta": "g", "gamma": "g", "theta": "g", "vega": "g",
}

_DASH = "—"


def kind(field: str) -> str:
    return _KIND[field]


def cell_value(field: str, q: OptionQuote | None, spot: float | None, dte: int) -> float | None:
    """Raw numeric value for one (field, quote) given the chain context, or None if N/A."""
    if q is None:
        return None
    if field == "volume":
        return q.volume
    if field == "oi":
        return q.open_interest
    if field == "bid":
        return q.bid
    if field == "ask":
        return q.ask
    if field == "mark":
        return q.mark
    if field == "ltp":
        return q.last
    if field == "iv":
        return q.iv
    if field in ("delta", "gamma", "theta", "vega"):
        return getattr(q, field)
    if field == "distance":
        return None if spot is None else abs(q.strike - spot)
    if field == "reldist":
        return None if not spot else abs(q.strike - spot) / spot
    if field == "bidpct":
        return None if not spot or q.bid is None else q.bid / spot
    if field == "askpct":
        return None if not spot or q.ask is None else q.ask / spot
    if field == "spread":
        if q.bid is None or q.ask is None or not q.mark:
            return None
        return (q.ask - q.bid) / q.mark
    if field == "theor":
        t = dte / 365.0
        return black_scholes_price(spot, q.strike, t, q.iv, q.type)
    if field in ("annbid", "annask"):
        # annualized premium yield: (premium / strike) * (365 / days)
        premium = q.bid if field == "annbid" else q.ask
        if not premium or q.strike <= 0:
            return None
        return (premium / q.strike) * (365.0 / max(dte, 1))
    return None


def fmt(value: float | None, field: str) -> str:
    """Display string for a raw value per the field's kind."""
    if value is None:
        return _DASH
    k = _KIND[field]
    if k == "pct":
        return f"{value * 100:.2f}%"
    if k in ("int", "bar"):
        return f"{value:,.0f}"
    if k == "g":
        return f"{value:.3f}"
    return f"{value:,.2f}"  # px
