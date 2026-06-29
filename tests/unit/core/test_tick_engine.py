"""Tests for the per-tick engine loop: tick_to_bar + step_tick + run_ticks (Task 3, Slice 2)."""
from vike_trader_app.core.ticks import QuoteTick, TradeTick
from vike_trader_app.core.consolidator import tick_to_bar
from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.fill_model import TickFillModel
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy


def test_tick_to_bar_quote_and_trade():
    qb = tick_to_bar(QuoteTick(ts=5, bid=9.99, ask=10.01))
    assert (qb.ts, qb.open, qb.high, qb.low, qb.close) == (5, 10.0, 10.0, 10.0, 10.0)
    assert qb.bid == 9.99 and qb.ask == 10.01
    tb = tick_to_bar(TradeTick(ts=7, price=50.0, size=0.3))
    assert (tb.open, tb.high, tb.low, tb.close, tb.volume) == (50.0, 50.0, 50.0, 50.0, 0.3)
    assert tb.bid is None and tb.ask is None


class _BuyThenClose(Strategy):
    def on_quote_tick(self, tick):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


def test_market_fills_next_tick_crossing_spread():
    ticks = [QuoteTick(ts=0, bid=9.99, ask=10.01),
             QuoteTick(ts=1, bid=19.95, ask=20.05),
             QuoteTick(ts=2, bid=29.90, ask=30.10),
             QuoteTick(ts=3, bid=39.90, ask=40.10)]
    eng = SingleSymbolEngine([], _BuyThenClose(), fill_model=TickFillModel())
    result = eng.run_ticks(ticks)
    # buy submitted at tick0 -> fills tick1 ask 20.05; close (sell) at tick2 -> fills tick3 bid 39.90
    assert result.trades[0].entry_price == 20.05
    assert result.trades[0].exit_price == 39.90


class _TickCounter(Strategy):
    def __init__(self):
        self.quotes = 0
        self.trades_seen = 0
    def on_quote_tick(self, tick):
        self.quotes += 1
    def on_trade_tick(self, tick):
        self.trades_seen += 1
    def on_bar(self, bar):
        raise AssertionError("on_bar must NOT fire in tick mode")


def test_dispatch_by_type_and_no_on_bar():
    s = _TickCounter()
    SingleSymbolEngine([], s, fill_model=TickFillModel()).run_ticks(
        [QuoteTick(ts=0, bid=1, ask=1), TradeTick(ts=1, price=1, size=1)])
    assert s.quotes == 1 and s.trades_seen == 1


class _StopAndTarget(Strategy):
    """Long at tick0; arm a protective sell-stop at 9.0 and a sell limit (target) at 12.0."""
    def on_quote_tick(self, tick):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.stop_sell(1.0, 9.0)     # protective stop
            self.limit_sell(1.0, 12.0)   # take-profit


def test_magnifier_resolves_stop_vs_target_by_real_sequence():
    # entry fills tick1; then price hits the TARGET (12) at tick2 BEFORE the stop (9) at tick3.
    ticks = [QuoteTick(ts=0, bid=10, ask=10), QuoteTick(ts=1, bid=10, ask=10),
             QuoteTick(ts=2, bid=12, ask=12), QuoteTick(ts=3, bid=9, ask=9)]
    eng = SingleSymbolEngine([], _StopAndTarget(), fill_model=TickFillModel())
    result = eng.run_ticks(ticks)
    assert result.trades[-1].exit_price == 12.0      # target hit first (real sequence)
    assert result.intrabar_both_hit == 0             # no ambiguity: ticks gave the true order
