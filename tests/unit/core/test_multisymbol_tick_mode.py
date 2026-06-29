"""Tests for MultiSymbolEngine.run_ticks — tick-mode multi-symbol capability.

TDD suite covering:
  1. N=1 tick parity: single-symbol scenario through both engines → equal equity + trade PnLs.
  2. 2-symbol interleaved: ticks route to the correct SymbolState and total equity is correct.
  3. tick.symbol field additive check: single-symbol code still works with default symbol="".
"""

import pytest

from vike_trader_app.core.ticks import QuoteTick, TradeTick
from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine, SymbolState
from vike_trader_app.core.fill_model import TickFillModel
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _qt(ts, bid, ask, symbol=""):
    return QuoteTick(ts=ts, bid=bid, ask=ask, symbol=symbol)


def _tt(ts, price, size=1.0, symbol=""):
    return TradeTick(ts=ts, price=price, size=size, symbol=symbol)


# ---------------------------------------------------------------------------
# N=1 tick parity
# Strategy for MultiSymbolEngine — uses unified symbol-explicit API
# ---------------------------------------------------------------------------

class _MultiSymBuyThenClose(Strategy):
    """Buy X at tick 0, close at tick 2."""
    def on_quote_tick(self, tick):
        if self.index == 0:
            self.buy("X", 1.0)
        elif self.index == 2:
            self.close("X")


class _SingleSymBuyThenClose(SingleSymbolStrategy):
    """Same scenario for SingleSymbolEngine (no symbol arg to verbs)."""
    def on_quote_tick(self, tick):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 2:
            self.close()


CASH = 10_000.0

# Four quote ticks: buy at tick0 (fills @tick1 ask 20.05), close at tick2 (fills @tick3 bid 39.90)
QUOTE_TICKS_SINGLE = [
    _qt(0,  9.99,  10.01),
    _qt(1,  19.95, 20.05),
    _qt(2,  29.90, 30.10),
    _qt(3,  39.90, 40.10),
]

QUOTE_TICKS_X = [_qt(t.ts, t.bid, t.ask, symbol="X") for t in QUOTE_TICKS_SINGLE]


def _run_single():
    eng = SingleSymbolEngine([], _SingleSymBuyThenClose(), cash=CASH, fill_model=TickFillModel())
    result = eng.run_ticks(QUOTE_TICKS_SINGLE)
    return eng, result


def _make_portfolio_n1():
    """MultiSymbolEngine in tick-only mode with a single symbol X."""
    strat = _MultiSymBuyThenClose()
    eng = MultiSymbolEngine(
        {},  # tick-only: no bar series
        strat,
        cash=CASH,
        fill_model=TickFillModel(),
    )
    # Inject SymbolState for "X" (bars_by_symbol={} so __init__ built no _sym entries).
    eng.symbols = ["X"]
    eng._sym = {"X": SymbolState()}
    return eng


def _run_portfolio_n1():
    eng = _make_portfolio_n1()
    result = eng.run_ticks({"X": QUOTE_TICKS_X})
    return eng, result


def test_n1_tick_final_equity_equal():
    _, sr = _run_single()
    _, pr = _run_portfolio_n1()
    assert pr.final_equity == pytest.approx(sr.final_equity, rel=1e-9), (
        f"single={sr.final_equity}, portfolio={pr.final_equity}"
    )


def test_n1_tick_equity_curve_match():
    _, sr = _run_single()
    _, pr = _run_portfolio_n1()
    assert len(pr.equity_curve) == len(sr.equity_curve)
    for i, (e, p) in enumerate(zip(sr.equity_curve, pr.equity_curve)):
        assert p == pytest.approx(e, rel=1e-9), f"tick {i}: single={e}, portfolio={p}"


def test_n1_tick_one_trade_each():
    _, sr = _run_single()
    _, pr = _run_portfolio_n1()
    assert len(sr.trades) == 1
    assert len(pr.trades) == 1


def test_n1_tick_trade_pnl_equal():
    _, sr = _run_single()
    _, pr = _run_portfolio_n1()
    assert pr.trades[0].pnl == pytest.approx(sr.trades[0].pnl, rel=1e-9), (
        f"single pnl={sr.trades[0].pnl}, portfolio pnl={pr.trades[0].pnl}"
    )


def test_n1_tick_entry_exit_prices_match():
    _, sr = _run_single()
    _, pr = _run_portfolio_n1()
    assert pr.trades[0].entry_price == pytest.approx(sr.trades[0].entry_price)
    assert pr.trades[0].exit_price == pytest.approx(sr.trades[0].exit_price)


# ---------------------------------------------------------------------------
# 2-symbol interleaved test
# ---------------------------------------------------------------------------

class _TwoSymStrategy(Strategy):
    """Buy X at X-tick-0, buy Y at Y-tick-1. Tracks which symbols fired on_quote_tick."""

    def __init__(self):
        super().__init__()
        self.seen: list[str] = []   # symbol for each on_quote_tick call in order
        self.seen_ts: list[int] = []
        self._x_count = 0
        self._y_count = 0

    def on_quote_tick(self, tick):
        self.seen.append(tick.symbol)
        self.seen_ts.append(tick.ts)
        if tick.symbol == "X":
            if self._x_count == 0:
                self.buy("X", 1.0)   # buy at X-tick-0: fills at X-tick-1 (next X tick)
            self._x_count += 1
        elif tick.symbol == "Y":
            if self._y_count == 1:
                self.buy("Y", 1.0)   # buy at Y-tick-1: fills at Y-tick-2 (next Y tick)
            self._y_count += 1


def _make_2sym_engine(strategy=None):
    if strategy is None:
        strategy = _TwoSymStrategy()
    eng = MultiSymbolEngine(
        {},  # tick-only
        strategy,
        cash=CASH,
        fill_model=TickFillModel(),
    )
    eng.symbols = ["X", "Y"]
    eng._sym = {"X": SymbolState(), "Y": SymbolState()}
    return eng


# Interleaved ticks: alternating X and Y, sorted by ts
# X: ts=0, 2, 4, 6  Y: ts=1, 3, 5, 7
X_TICKS = [
    _qt(0, 99.0, 101.0, symbol="X"),   # X tick 0: buy X submitted
    _qt(2, 99.0, 101.0, symbol="X"),   # X tick 1: buy X fills @ask=101
    _qt(4, 109.0, 111.0, symbol="X"),  # X tick 2: hold
    _qt(6, 109.0, 111.0, symbol="X"),  # X tick 3: hold
]

Y_TICKS = [
    _qt(1, 49.0, 51.0, symbol="Y"),    # Y tick 0: hold
    _qt(3, 49.0, 51.0, symbol="Y"),    # Y tick 1: buy Y submitted
    _qt(5, 49.0, 51.0, symbol="Y"),    # Y tick 2: buy Y fills @ask=51
    _qt(7, 59.0, 61.0, symbol="Y"),    # Y tick 3: hold
]


def test_2sym_ticks_fire_in_ts_order():
    """Each tick fires on_quote_tick for the correct symbol in ts order."""
    strat = _TwoSymStrategy()
    eng = _make_2sym_engine(strat)
    eng.run_ticks({"X": X_TICKS, "Y": Y_TICKS})
    # Merged ts order: 0(X),1(Y),2(X),3(Y),4(X),5(Y),6(X),7(Y)
    expected_syms = ["X", "Y", "X", "Y", "X", "Y", "X", "Y"]
    expected_ts   = [0,   1,   2,   3,   4,   5,   6,   7  ]
    assert strat.seen == expected_syms, f"got {strat.seen}"
    assert strat.seen_ts == expected_ts, f"got {strat.seen_ts}"


def test_2sym_fills_land_on_correct_state():
    """Fills for X land on X's SymbolState; fills for Y land on Y's SymbolState."""
    strat = _TwoSymStrategy()
    eng = _make_2sym_engine(strat)
    eng.run_ticks({"X": X_TICKS, "Y": Y_TICKS})
    # X: buy submitted at X-tick-0 (ts=0), fills at X-tick-1 (ts=2) @ask=101
    assert eng._sym["X"].pos.size == pytest.approx(1.0)
    assert eng._sym["X"].pos.avg_price == pytest.approx(101.0)
    # Y: buy submitted at Y-tick-1 (ts=3), fills at Y-tick-2 (ts=5) @ask=51
    assert eng._sym["Y"].pos.size == pytest.approx(1.0)
    assert eng._sym["Y"].pos.avg_price == pytest.approx(51.0)
    # No closed trades (positions still open)
    assert len(eng.trades) == 0


def test_2sym_total_equity_correct():
    """Total equity = cash - cost_X - cost_Y + pos_X*price_X + pos_Y*price_Y after all ticks."""
    strat = _TwoSymStrategy()
    eng = _make_2sym_engine(strat)
    result = eng.run_ticks({"X": X_TICKS, "Y": Y_TICKS})
    # After all ticks: X last price = mid(109,111)=110, Y last price = mid(59,61)=60
    # cash paid: 101 (X buy) + 51 (Y buy)
    # equity = (CASH - 101 - 51) + 1*110 + 1*60
    expected_equity = CASH - 101.0 - 51.0 + 1.0 * 110.0 + 1.0 * 60.0
    assert result.final_equity == pytest.approx(expected_equity, rel=1e-6), (
        f"expected ~{expected_equity}, got {result.final_equity}"
    )


# ---------------------------------------------------------------------------
# tick.symbol additive: default symbol="" preserves single-symbol paths
# ---------------------------------------------------------------------------

def test_quote_tick_default_symbol_empty():
    t = QuoteTick(ts=1, bid=9.99, ask=10.01)
    assert t.symbol == ""


def test_trade_tick_default_symbol_empty():
    t = TradeTick(ts=1, price=10.0, size=1.0)
    assert t.symbol == ""


def test_single_symbol_engine_unaffected_by_symbol_field():
    """SingleSymbolEngine.run_ticks still works with ticks carrying default symbol=""."""
    class _Buy(SingleSymbolStrategy):
        def on_quote_tick(self, tick):
            if self.index == 0:
                self.buy(1.0)
    ticks = [QuoteTick(ts=0, bid=9, ask=11), QuoteTick(ts=1, bid=19, ask=21)]
    eng = SingleSymbolEngine([], _Buy(), fill_model=TickFillModel())
    result = eng.run_ticks(ticks)
    # buy at tick0 fills at tick1 @ask=21; position open
    assert len(result.equity_curve) == 2
    assert eng.position.size == pytest.approx(1.0)
