"""Test Strategy tick hooks (no-op defaults for Slice 2 groundwork)."""

from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.ticks import QuoteTick, TradeTick


def test_default_tick_hooks_are_noops():
    s = Strategy()
    assert s.on_quote_tick(QuoteTick(ts=0, bid=1, ask=1)) is None
    assert s.on_trade_tick(TradeTick(ts=0, price=1, size=1)) is None


def test_tick_hooks_are_overridable():
    seen = []

    class S(Strategy):
        def on_quote_tick(self, tick):
            seen.append(("q", tick.ts))

    S().on_quote_tick(QuoteTick(ts=5, bid=1, ask=1))
    assert seen == [("q", 5)]
