"""Tests for the new unified per-symbol Strategy class (Task 6).

on_bar receives one Bar per symbol (with bar.symbol set); symbol-explicit verbs
place orders and return OrderHandle; reads are symbol-keyed; reserved tick
handlers are no-ops.
"""
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy
from vike_trader_app.core.portfolio import PortfolioEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.order_handle import OrderHandle
from vike_trader_app.core.schedule import MonthStart


def _series(n, base=10):
    return [Bar(ts=i * 60_000, open=base, high=base + 1, low=base - 1, close=base) for i in range(n)]


# ---------------------------------------------------------------------------
# Basic fan-out: on_bar fires once per symbol per step, bar.symbol is set
# ---------------------------------------------------------------------------

def test_on_bar_fires_per_symbol_with_symbol_on_bar():
    seen = []

    class S(Strategy):
        def on_bar(self, bar):
            seen.append(bar.symbol)

    PortfolioEngine(
        {"BTC": _series(2), "ETH": _series(2)},
        S(),
        fee_rate=0.0,
        cash=1000,
        default_venue="binance",
    ).run()
    assert set(seen) == {"BTC.BINANCE", "ETH.BINANCE"}
    assert len(seen) == 4  # 2 symbols × 2 bars


# ---------------------------------------------------------------------------
# Symbol-explicit buy takes a position
# ---------------------------------------------------------------------------

def test_buy_is_symbol_explicit_and_takes_position():
    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(bar.symbol, 1.0)

    eng = PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    assert eng._sym["BTC"].pos.size == 1.0


# ---------------------------------------------------------------------------
# Reserved tick handlers are no-ops (return None)
# ---------------------------------------------------------------------------

def test_reserved_tick_handlers_are_noops():
    s = Strategy()
    assert s.on_quote_tick(None) is None
    assert s.on_trade_tick(None) is None
    assert s.on_order_book(None) is None


# ---------------------------------------------------------------------------
# Strategy is NOT the compat alias any more
# ---------------------------------------------------------------------------

def test_strategy_is_not_single_symbol_strategy():
    assert Strategy is not SingleSymbolStrategy


def test_new_strategy_has_per_symbol_on_bar():
    """on_bar on the new Strategy takes a single bar (not ts+bars dict)."""
    import inspect
    sig = inspect.signature(Strategy.on_bar)
    params = list(sig.parameters)
    # (self, bar) — 'bar' is the only non-self param
    assert params == ["self", "bar"]


def test_new_strategy_has_reserved_handlers():
    assert hasattr(Strategy, "on_quote_tick")
    assert hasattr(Strategy, "on_trade_tick")
    assert hasattr(Strategy, "on_order_book")


# ---------------------------------------------------------------------------
# position(symbol) read returns the live Position
# ---------------------------------------------------------------------------

def test_position_read_by_symbol():
    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(bar.symbol, 2.0)

    eng = PortfolioEngine({"ETH": _series(3)}, S(), fee_rate=0.0, cash=1000)
    result_pos = []

    class S2(Strategy):
        def on_bar(self, bar):
            if self.index == 1:
                result_pos.append(self.position(bar.symbol).size)

    eng2 = PortfolioEngine({"ETH": _series(3)}, S2(), fee_rate=0.0, cash=1000)

    class S3(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(bar.symbol, 2.0)
            if self.index == 1:
                result_pos.append(self.position(bar.symbol).size)

    eng3 = PortfolioEngine({"ETH": _series(3)}, S3(), fee_rate=0.0, cash=1000)
    eng3.run()
    assert result_pos == [2.0]


# ---------------------------------------------------------------------------
# order_target_percent drives the cleaned _engine_target
# ---------------------------------------------------------------------------

def test_order_target_percent():
    """After targeting 50% of equity, the position should be non-zero."""
    reached = []

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                self.order_target_percent(bar.symbol, 0.5)
            if self.index == 1:
                reached.append(self.position(bar.symbol).size)

    eng = PortfolioEngine({"BTC": _series(3, base=100)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    # equity=1000, price=100 → target = 0.5*1000/100 = 5.0 shares
    assert reached and reached[0] == 5.0


# ---------------------------------------------------------------------------
# buy() returns an OrderHandle; .cancel() removes it before it fills
# ---------------------------------------------------------------------------

def test_buy_returns_order_handle_and_cancel_removes_it():
    handles = []

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                # limit order far below price so it won't fill immediately
                h = self.buy(bar.symbol, 1.0, limit=1.0)
                handles.append(h)
                # cancel before it can fill
                h.cancel()

    eng = PortfolioEngine({"BTC": _series(5, base=10)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    assert len(handles) == 1
    assert isinstance(handles[0], OrderHandle)
    # position stayed flat — limit was cancelled
    assert eng._sym["BTC"].pos.size == 0.0


# ---------------------------------------------------------------------------
# equity and drawdown properties are accessible from on_bar
# ---------------------------------------------------------------------------

def test_equity_and_drawdown_readable():
    equities = []
    dds = []

    class S(Strategy):
        def on_bar(self, bar):
            equities.append(self.equity)
            dds.append(self.drawdown)

    eng = PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=500)
    eng.run()
    assert len(equities) == 3
    assert all(e > 0 for e in equities)
    assert all(0.0 <= d <= 1.0 for d in dds)


# ---------------------------------------------------------------------------
# history() raises a clear deferral error (engine wiring is a follow-up slice)
# ---------------------------------------------------------------------------

def test_history_raises_notimplemented_pending_wiring():
    import pytest
    with pytest.raises(NotImplementedError):
        Strategy().history("BTC", "1h", 10)


# ---------------------------------------------------------------------------
# symbols attribute mirrors engine symbols
# ---------------------------------------------------------------------------

def test_symbols_attr():
    syms_seen = []

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 0:
                syms_seen.extend(self.symbols)

    eng = PortfolioEngine({"A": _series(2), "B": _series(2)}, S(), fee_rate=0.0, cash=1000)
    eng.run()
    assert set(syms_seen) == {"A", "B"}


# ---------------------------------------------------------------------------
# Lifecycle: on_start fires before the loop, on_stop after, on_fill per fill
# ---------------------------------------------------------------------------

def test_lifecycle_fires():
    ev = []

    class S(Strategy):
        def on_start(self): ev.append("start")
        def on_stop(self): ev.append("stop")
        def on_fill(self, fill): ev.append("fill")
        def on_bar(self, bar):
            if self.index == 0:
                self.buy(bar.symbol, 1.0)

    PortfolioEngine({"BTC": _series(3)}, S(), fee_rate=0.0, cash=1000).run()
    assert ev[0] == "start" and ev[-1] == "stop" and "fill" in ev


# ---------------------------------------------------------------------------
# Schedule.On: monthly callback fires at least twice over 70 daily bars
# ---------------------------------------------------------------------------

def test_schedule_fires_once_per_period():
    """A Strategy registers a monthly callback in on_start; it fires once per month boundary."""
    fired = []

    class S(Strategy):
        def on_start(self):
            self.schedule.on(MonthStart(), lambda: fired.append(self.index))

        def on_bar(self, bar):
            pass

    # 70 daily bars (ms spacing = 86_400_000) spanning >2 months
    bars = [Bar(ts=i * 86_400_000, open=1, high=1, low=1, close=1) for i in range(70)]
    PortfolioEngine({"BTC": bars}, S(), fee_rate=0.0, cash=1000).run()
    assert len(fired) >= 2  # at least 2 month boundaries crossed


# ---------------------------------------------------------------------------
# N-invariance golden test (goals 1+2): one Strategy, same per-symbol result
# at N=1 and as one symbol of N>1
# ---------------------------------------------------------------------------

def test_n_invariance_btc_subresult_matches_standalone():
    """A strategy authored once produces the same BTC result at N=1 and as 1 of N (goals 1+2).

    Fixed-size market orders + fee_rate=0 + default engine settings (no cash_gate,
    no leverage cap, no max_open_positions) mean BTC PnL is fully isolated and must
    be EXACTLY equal whether the engine runs solo (N=1) or alongside ETH (N=2).
    We use a price-varying BTC series so the BTC trade has non-zero PnL, making the
    equality assertion meaningful.
    """

    class Cross(Strategy):
        def on_bar(self, bar):
            if self.index == 2:
                self.buy(bar.symbol, 1.0)
            elif self.index == 5:
                self.close(bar.symbol)

    # BTC: prices rise from 10 to 15 between entry-fill bar (index 3 open=12) and
    # exit-fill bar (index 6 open=15), so the trade yields a non-zero realized PnL.
    # Layout: buy submitted at index 2 → fills at index 3's open (12).
    #         close submitted at index 5 → fills at index 6's open (15).
    # PnL = (15 - 12) * 1.0 = 3.0  (with fee_rate=0.0)
    btc_prices = [10, 10, 10, 12, 13, 14, 15, 15]  # 8 bars, open=close=price
    btc = [Bar(ts=i * 60_000, open=p, high=p + 1, low=p - 1, close=p) for i, p in enumerate(btc_prices)]

    # ETH: flat series — ensures BTC PnL cannot depend on ETH's price path
    eth_prices = [20] * 8
    eth = [Bar(ts=i * 60_000, open=p, high=p + 1, low=p - 1, close=p) for i, p in enumerate(eth_prices)]

    solo = PortfolioEngine({"BTC": list(btc)}, Cross(), fee_rate=0.0, cash=10_000).run()
    multi = PortfolioEngine({"BTC": list(btc), "ETH": list(eth)}, Cross(), fee_rate=0.0, cash=10_000).run()

    # BTC trade must have non-zero PnL (entry open=12, exit open=15, size=1.0 → PnL=3.0)
    assert solo.per_symbol_pnl["BTC"] != 0.0, (
        f"BTC PnL is zero ({solo.per_symbol_pnl['BTC']}); series must have price variation "
        "between entry-fill and exit-fill bars so the equality test is meaningful"
    )
    # The core N-invariance assertion: per-symbol BTC PnL must be EXACTLY equal
    assert solo.per_symbol_pnl["BTC"] == multi.per_symbol_pnl["BTC"], (
        f"N-invariance FAILED: solo BTC PnL={solo.per_symbol_pnl['BTC']}, "
        f"multi BTC PnL={multi.per_symbol_pnl['BTC']} — fixed-size isolated trades "
        "must not depend on N"
    )
