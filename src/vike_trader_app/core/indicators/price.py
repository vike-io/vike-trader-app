"""Price transform indicators (no warm-up period — each bar is fully defined)."""

from .base import indicator


@indicator(category="price", inputs=["open", "high", "low", "close"], params=[], outputs=["avgprice"])
def avgprice(opens, highs, lows, closes):
    """Average price: ``(O + H + L + C) / 4`` for every bar."""
    return [(opens[i] + highs[i] + lows[i] + closes[i]) / 4.0 for i in range(len(closes))]


@indicator(category="price", inputs=["high", "low"], params=[], outputs=["medprice"])
def medprice(highs, lows):
    """Median price: ``(H + L) / 2`` for every bar."""
    return [(highs[i] + lows[i]) / 2.0 for i in range(len(highs))]


@indicator(category="price", inputs=["high", "low", "close"], params=[], outputs=["typprice"])
def typprice(highs, lows, closes):
    """Typical price: ``(H + L + C) / 3`` for every bar."""
    return [(highs[i] + lows[i] + closes[i]) / 3.0 for i in range(len(closes))]


@indicator(category="price", inputs=["high", "low", "close"], params=[], outputs=["wclprice"])
def wclprice(highs, lows, closes):
    """Weighted close price: ``(H + L + 2*C) / 4`` for every bar."""
    return [(highs[i] + lows[i] + 2.0 * closes[i]) / 4.0 for i in range(len(closes))]
