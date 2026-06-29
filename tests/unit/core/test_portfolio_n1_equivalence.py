"""P0-D equivalence gate: MultiSymbolEngine@N=1 == SingleSymbolEngine for a SL+TP bracket scenario.

This is the load-bearing correctness gate for retiring SingleSymbolEngine.

Scenario (mirrors _BracketLong from test_intrabar_gap_fills.py):
  bar 0: buy submitted
  bar 1: buy fills @100; arm stop_sell @95 (SL) AND limit_sell @110 (TP)
  bar 2: BOTH hit on the same bar (high 112 >= 110 TP, low 94 <= 95 SL)
         -> adverse-first resolution: stop fills at 95, limit capped to 0 (both_hit=1)
  bar 3: flat, nothing

Expected AFTER the fix:
  - Both engines produce exactly 1 closing trade (no over-close or flip)
  - Exit price == 95.0 (stop-first / pessimistic)
  - Trade PnL == -5.0 (loss)
  - intrabar_both_hit == 1 on both engines
  - Final equity curves equal bar-by-bar

BEFORE the fix (MultiSymbolEngine had no resolution), MultiSymbolEngine would have filled
BOTH the stop_sell and the limit_sell uncapped (pending-order sequence), producing 2
trades and a spurious flip/over-close — so this test would FAIL on the portfolio side.
"""

import pytest

from vike_trader_app.core.single_symbol_engine import SingleSymbolEngine
from vike_trader_app.core.model import Bar
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.core.portfolio_adapter import MultiSymbolStrategyRunner
from vike_trader_app.tester.config import TesterConfig


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _bar(ts, o, h, l, c):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=1.0)


class _BracketLong(Strategy):
    """Buy on bar 0; arm SL @95 + TP @110 on bar 1; both hit on bar 2."""

    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 1:
            self.stop_sell(1.0, price=95.0)    # stop-loss @95
            self.limit_sell(1.0, price=110.0)  # take-profit @110


BARS = [
    _bar(0,        100, 101, 99,  100),   # bar 0: buy submitted
    _bar(60_000,   100, 100, 100, 100),   # bar 1: buy fills @100; bracket armed
    _bar(120_000,  100, 112, 94,  100),   # bar 2: BOTH triggered (high 112>=110, low 94<=95)
    _bar(180_000,  100, 101, 99,  100),   # bar 3: flat, trailing bar
]

CASH = 10_000.0


# ---------------------------------------------------------------------------
# SingleSymbolEngine reference run
# ---------------------------------------------------------------------------

def _run_engine():
    eng = SingleSymbolEngine(list(BARS), _BracketLong(), cash=CASH)
    result = eng.run()
    return eng, result


# ---------------------------------------------------------------------------
# MultiSymbolEngine N=1 run (via MultiSymbolStrategyRunner)
# ---------------------------------------------------------------------------

def _run_portfolio():
    runner = MultiSymbolStrategyRunner(
        _BracketLong,
        {"X": list(BARS)},
        TesterConfig(cash=CASH),
    )
    result = runner.run()
    return runner._engine, result


# ---------------------------------------------------------------------------
# N=1 equivalence gate
# ---------------------------------------------------------------------------

def test_n1_bracket_engine_and_portfolio_equal_equity():
    """Final equity must be identical between SingleSymbolEngine and MultiSymbolEngine@N=1."""
    _, eng_res = _run_engine()
    _, port_res = _run_portfolio()
    assert port_res.final_equity == pytest.approx(eng_res.final_equity, rel=1e-9), (
        f"engine final_equity={eng_res.final_equity}, portfolio final_equity={port_res.final_equity}"
    )


def test_n1_bracket_equity_curves_match_bar_by_bar():
    """Equity curves must match bar-by-bar."""
    _, eng_res = _run_engine()
    _, port_res = _run_portfolio()
    assert len(port_res.equity_curve) == len(eng_res.equity_curve)
    for i, (e, p) in enumerate(zip(eng_res.equity_curve, port_res.equity_curve)):
        assert p == pytest.approx(e, rel=1e-9), (
            f"bar {i}: engine equity={e}, portfolio equity={p}"
        )


def test_n1_bracket_exactly_one_trade():
    """Both engines must produce exactly one trade — no over-close or spurious flip."""
    eng, eng_res = _run_engine()
    port_eng, port_res = _run_portfolio()
    assert len(eng_res.trades) == 1, f"engine produced {len(eng_res.trades)} trades, expected 1"
    assert len(port_res.trades) == 1, f"portfolio produced {len(port_res.trades)} trades, expected 1"


def test_n1_bracket_exit_at_stop_price():
    """Exit must be at the stop (95.0), not the limit (110.0) — adverse-first."""
    _, eng_res = _run_engine()
    _, port_res = _run_portfolio()
    assert eng_res.trades[0].exit_price == pytest.approx(95.0)
    assert port_res.trades[0].exit_price == pytest.approx(95.0), (
        f"portfolio exit at {port_res.trades[0].exit_price}, expected 95.0"
    )


def test_n1_bracket_trade_pnl_equal():
    """Trade PnL must be equal on both engines (buy @100, stop @95 -> -5.0 per unit)."""
    _, eng_res = _run_engine()
    _, port_res = _run_portfolio()
    assert eng_res.trades[0].pnl == pytest.approx(-5.0)
    assert port_res.trades[0].pnl == pytest.approx(eng_res.trades[0].pnl, rel=1e-9)


def test_n1_bracket_both_hit_flagged():
    """intrabar_both_hit must be 1 on both engines — the adversarial bar was exercised."""
    _, eng_res = _run_engine()
    _, port_res = _run_portfolio()
    assert eng_res.intrabar_both_hit == 1
    assert port_res.intrabar_both_hit == 1, (
        f"portfolio intrabar_both_hit={port_res.intrabar_both_hit}, expected 1"
    )


def test_n1_bracket_position_flat_after_run():
    """Position must be flat at end of run on both engines."""
    eng, _ = _run_engine()
    port_eng, _ = _run_portfolio()
    assert eng.position.size == pytest.approx(0.0)
    assert port_eng.position_of("X").size == pytest.approx(0.0)
