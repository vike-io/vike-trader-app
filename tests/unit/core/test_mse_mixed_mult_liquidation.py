"""TDD: 7 residual scalar self.multiplier reads in MultiSymbolEngine liquidation/sizing paths.

These tests FAIL on the pre-fix code (scalar self.multiplier used in _check_liquidation,
_check_liquidation_tick, and _size_entry) and PASS after replacing each with
self.multiplier_of(symbol/s).

Mixed-multiplier basket: A has multiplier=1, B has multiplier=10 (10x heavier per contract).
Uniform baskets are unaffected (no-op for the existing goldens).

LIQUIDATION THRESHOLD DERIVATION (bar mode):
  Setup: multipliers={A:1, B:10}; scalar fallback=1
  cash = 121; buy 1 of A @ open=100 (cost=100), buy 1 of B @ open=1 (cost=1*10=10)
  After fills: cash = 121 - 100 - 10 = 11

  bar1 lows: A_low=100 (doesn't move), B_low=1 (already bottomed)

  With PER-SYMBOL multipliers (correct, post-fix):
    eq_adv  = 11 + 1*100*1 + 1*1*10 = 11 + 100 + 10 = 121
    not_adv = |1|*100*1 + |1|*1*10  = 100 + 10      = 110
    maint_margin=1.1: threshold=1.1*110=121; eq_adv=121 <= 121 → FIRES (exact edge) ✓

  With SCALAR multiplier=1 for both (buggy, pre-fix):
    eq_adv  = 11 + 1*100*1 + 1*1*1  = 11 + 100 + 1  = 112
    not_adv = |1|*100*1 + |1|*1*1   = 100 + 1        = 101
    maint_margin=1.1: threshold=1.1*101=111.1; eq_adv=112 > 111.1 → NO fire ✓

So the test correctly distinguishes the two paths.
"""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine, PortfolioStrategy, SymbolState
from vike_trader_app.core.ticks import TradeTick, QuoteTick
from vike_trader_app.core.fill_model import TickFillModel
from vike_trader_app.core.sizing import SizeContext, PositionSizer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(ts, o, h, l, c, vol=1_000.0):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=vol)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

class _BuyBoth(PortfolioStrategy):
    """Buy 1 of A and 1 of B on bar 0 (market orders fill at bar 1's open)."""
    def on_bar(self, ts, bars):
        if self.index == 0:
            self.buy("A", 1.0)
            self.buy("B", 1.0)


# ---------------------------------------------------------------------------
# Liquidation — BAR MODE (_check_liquidation)
# ---------------------------------------------------------------------------
# Bar sequence:
#   bar0: strategy issues buys (market orders queued)
#   bar1: open_A=100, open_B=1 → fills execute; low_A=100, low_B=1 → liq check
#         After fills: cash = 121 - 100*1 - 1*10 = 11
#         Correct: eq_adv=121, not_adv=110, mm=1.1 → 121≤121 → liq FIRES
#         Buggy:   eq_adv=112, not_adv=101, mm=1.1 → 112>111.1 → liq does NOT fire
#   bar2: needed to check final state

def _make_bars():
    return {
        "A": [
            _bar(0,       100, 101, 99,  100),   # bar0: strategy issues buys
            _bar(60_000,  100, 101, 100, 100),   # bar1: fills at open=100; low=100 (adverse for long)
            _bar(120_000, 100, 101, 100, 100),   # bar2: end
        ],
        "B": [
            _bar(0,       1,   2,   0.5, 1),     # bar0
            _bar(60_000,  1,   1,   1,   1),     # bar1: fills at open=1; low=1 (adverse for long)
            _bar(120_000, 1,   1,   1,   1),     # bar2: end
        ],
    }


def test_bar_liq_fires_with_per_symbol_mult():
    """With correct per-symbol multipliers, liquidation fires and both positions are closed."""
    eng = MultiSymbolEngine(
        _make_bars(), _BuyBoth(),
        cash=121.0,
        multiplier=1,
        multipliers={"A": 1.0, "B": 10.0},
        maint_margin=1.1,
        fee_rate=0.0,
    )
    result = eng.run()

    # After liquidation fires on bar1: all positions are force-closed
    # → each position becomes a completed trade (entry + liq exit)
    assert len(result.trades) == 2, (
        f"Expected 2 completed round-trip trades (liq fired for both symbols), "
        f"got {len(result.trades)}: {result.trades}"
    )
    # Positions should be flat after liq
    assert eng._sym["A"].pos.size == 0, f"A still open after expected liquidation"
    assert eng._sym["B"].pos.size == 0, f"B still open after expected liquidation"


def test_bar_liq_does_not_fire_under_scalar_multiplier():
    """Scalar multiplier=1 for both symbols: no liquidation fires at mm=1.1.

    With scalar=1: eq_adv=112, mm*not_adv=111.1 → 112 > 111.1 → no liq.
    This is the PRE-FIX (buggy) behaviour — simulated by multipliers={}.
    """
    eng = MultiSymbolEngine(
        _make_bars(), _BuyBoth(),
        cash=121.0,
        multiplier=1,
        multipliers={},          # no per-symbol overrides → scalar=1 for all (pre-fix state)
        maint_margin=1.1,
        fee_rate=0.0,
    )
    result = eng.run()

    # No liq: positions opened but never closed → 0 completed trades
    assert len(result.trades) == 0, (
        f"Expected 0 completed trades (no liq under scalar=1), got {len(result.trades)}: "
        f"{result.trades}"
    )
    # Both positions remain open at end of run
    assert eng._sym["A"].pos.size != 0, "A position unexpectedly flat (liq fired when it shouldn't)"
    assert eng._sym["B"].pos.size != 0, "B position unexpectedly flat (liq fired when it shouldn't)"


# ---------------------------------------------------------------------------
# Liquidation — TICK MODE (_check_liquidation_tick)
# ---------------------------------------------------------------------------
# Strategy buys B on first B tick, buys A on first A tick.
# Market orders need to fill on a subsequent tick.
# The liq check fires on a later B tick at adverse=1.
#
# TICK LIQUIDATION CHECK for symbol B (adverse=1, OTHER symbol A at price=100):
#   eq_adv = cash + pos_B*adverse*mult_B + pos_A*price_A*mult_A
#   Correct:  eq_adv = 11 + 1*1*10 + 1*100*1 = 121; not_adv = 10 + 100 = 110
#             maint_margin=1.1 → threshold=121; eq_adv=121 ≤ 121 → FIRES ✓
#   Buggy:    eq_adv = 11 + 1*1*1  + 1*100*1 = 112; not_adv = 1 + 100 = 101
#             maint_margin=1.1 → threshold=111.1; eq_adv=112 > 111.1 → NO fire ✓
#
# We achieve cash=11 by: starting with cash=121, buying B@1 (cost=10) and A@100 (cost=100).
# In tick mode, market orders fill at the NEXT tick's price via _fill_pending_tick.
# We use QuoteTick so TickFillModel can cross the spread for market orders.
# Sequence:
#   ts=0:  B tick (bid=1, ask=1) → strategy issues buy B=1 (market order)
#   ts=10: A tick (bid=100, ask=100) → strategy issues buy A=1 (market order)
#   ts=20: B tick (bid=1, ask=1) → fills buy B @ ask=1 → cost=10 → cash=111
#   ts=30: A tick (bid=100, ask=100) → fills buy A @ ask=100 → cost=100 → cash=11
#   ts=40: B tick (bid=1, ask=1) → _check_liquidation_tick for B, adverse=1, other A @ price=100

class _BuyBothOnTick(PortfolioStrategy):
    """In tick mode: buy B on first B quote, buy A on first A quote."""
    def __init__(self):
        super().__init__()
        self._bought_b = False
        self._bought_a = False

    def on_quote_tick(self, tick):
        if tick.symbol == "B" and not self._bought_b:
            self.buy("B", 1.0)
            self._bought_b = True
        elif tick.symbol == "A" and not self._bought_a:
            self.buy("A", 1.0)
            self._bought_a = True


def _qt(ts, price, symbol):
    return QuoteTick(ts=ts, bid=price, ask=price, symbol=symbol)


def _make_tick_engine(*, cash=121.0, maint_margin=1.1, mixed=True):
    """Tick-mode engine: inject symbol states for A and B, then run ticks."""
    multipliers = {"A": 1.0, "B": 10.0} if mixed else {}
    eng = MultiSymbolEngine(
        {},  # no bars (tick-only run)
        _BuyBothOnTick(),
        cash=cash,
        multiplier=1,
        multipliers=multipliers,
        maint_margin=maint_margin,
        fee_rate=0.0,
        fill_model=TickFillModel(),
    )
    # Inject symbol states for tick-only run (pattern from test_multisymbol_tick_mode.py)
    eng.symbols = ["A", "B"]
    eng._sym = {"A": SymbolState(), "B": SymbolState()}

    ticks = {
        "A": [
            _qt(10,  100.0, "A"),   # ts=10:  strategy issues buy A
            _qt(30,  100.0, "A"),   # ts=30:  fills buy A @ ask=100 → cash 121-10-100=11
        ],
        "B": [
            _qt(0,   1.0,   "B"),   # ts=0:   strategy issues buy B
            _qt(20,  1.0,   "B"),   # ts=20:  fills buy B @ ask=1 → cost=10 → cash 121-10=111
            _qt(40,  1.0,   "B"),   # ts=40:  adverse tick → liq check for B
        ],
    }
    return eng, ticks


def test_tick_liq_fires_with_per_symbol_mult():
    """In tick mode with mixed multipliers, liquidation fires (some position closed).

    The liq threshold fires on the A fill tick (ts=30): once A fills at 100, cash=11 and:
      eq_adv = 11 + 1*100*1 + 1*1*10 = 121; not_adv = 110; mm=1.1 → 121 ≤ 121 → FIRES for A.

    This is the correct per-symbol behaviour. In tick mode, _check_liquidation_tick fires
    for the symbol whose tick just arrived; here the A tick at ts=30 triggers the check.
    The important invariant: the check fires (at least one position is force-closed),
    producing at least one completed trade in result.trades.
    """
    eng, ticks = _make_tick_engine(mixed=True)
    result = eng.run_ticks(ticks)

    # With correct per-symbol multipliers, liq fires → ≥1 completed round-trip trade
    assert len(result.trades) >= 1, (
        f"Expected >=1 completed trade (liq fired), got {len(result.trades)}. "
        f"A pos: {eng._sym['A'].pos.size}, B pos: {eng._sym['B'].pos.size}"
    )
    # At least one position must have been force-closed (flat)
    a_flat = eng._sym["A"].pos.size == 0
    b_flat = eng._sym["B"].pos.size == 0
    assert a_flat or b_flat, (
        f"Neither position is flat after expected liquidation: "
        f"A={eng._sym['A'].pos.size}, B={eng._sym['B'].pos.size}"
    )


def test_tick_liq_does_not_fire_under_scalar_multiplier():
    """In tick mode with scalar=1 for all, liquidation does NOT fire at mm=1.1.

    Buggy: eq_adv=112, mm*not_adv=111.1 → 112 > 111.1 → no liq.
    """
    eng, ticks = _make_tick_engine(mixed=False)  # no per-symbol overrides → scalar=1
    result = eng.run_ticks(ticks)

    # No liq → B stays open → 0 completed trades
    completed = [t for t in result.trades if t.exit_price is not None]
    assert len(completed) == 0, (
        f"Expected 0 completed trades under scalar=1 at mm=1.1, got {len(completed)}. "
        f"All trades: {result.trades}"
    )
    assert eng._sym["B"].pos.size != 0, (
        "B position unexpectedly flat (liq fired when scalar=1 shouldn't trigger it)"
    )


# ---------------------------------------------------------------------------
# Sizing path (_size_entry) — SizeContext.multiplier must be per-symbol
# ---------------------------------------------------------------------------

class _CaptureSizeCtx(PositionSizer):
    """Records every SizeContext, returns intent unchanged (PassThrough)."""
    def __init__(self):
        self.calls: list[SizeContext] = []

    def size(self, ctx: SizeContext) -> float:
        self.calls.append(ctx)
        return ctx.intent


class _BuyEachOnBar1(PortfolioStrategy):
    """Buy 1 A and 1 B on bar 1 (fills at bar 2's open; sizer is called during bar 1's on_bar)."""
    def on_bar(self, ts, bars):
        if self.index == 1:
            self.buy("A", 1.0)
            self.buy("B", 1.0)


def test_size_context_carries_per_symbol_multiplier():
    """_size_entry must pass multiplier_of(symbol) (not scalar self.multiplier) to SizeContext."""
    sizer = _CaptureSizeCtx()
    bars = {
        "A": [_bar(i * 60_000, 100, 101, 99, 100) for i in range(4)],
        "B": [_bar(i * 60_000, 10,  11,  9,  10)  for i in range(4)],
    }
    eng = MultiSymbolEngine(
        bars, _BuyEachOnBar1(),
        cash=100_000.0,
        multiplier=1,               # scalar default
        multipliers={"A": 1.0, "B": 10.0},
        fee_rate=0.0,
        sizer=sizer,
    )
    eng.run()

    assert len(sizer.calls) >= 2, f"Expected >=2 sizer calls, got {len(sizer.calls)}"
    by_sym = {ctx.symbol: ctx for ctx in sizer.calls}

    assert "A" in by_sym, f"No SizeContext captured for A. Symbols seen: {list(by_sym)}"
    assert "B" in by_sym, f"No SizeContext captured for B. Symbols seen: {list(by_sym)}"

    assert by_sym["A"].multiplier == pytest.approx(1.0), (
        f"A: expected multiplier=1.0, got {by_sym['A'].multiplier}"
    )
    assert by_sym["B"].multiplier == pytest.approx(10.0), (
        f"B: expected multiplier=10.0, got {by_sym['B'].multiplier} (scalar=1 bug not fixed?)"
    )
