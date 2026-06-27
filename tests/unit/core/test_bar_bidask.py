"""Test Bar bid/ask optional fields."""

from vike_trader_app.core.model import Bar


def test_bar_defaults_have_no_quote():
    """Bar with no bid/ask defaults to None (backward compatible)."""
    b = Bar(ts=1, open=1, high=2, low=0.5, close=1.5)
    assert b.bid is None and b.ask is None  # backward compatible


def test_bar_carries_bid_ask():
    """Bar can carry explicit bid/ask values."""
    b = Bar(ts=1, open=1, high=2, low=0.5, close=1.5, bid=1.49, ask=1.51)
    assert b.bid == 1.49 and b.ask == 1.51
