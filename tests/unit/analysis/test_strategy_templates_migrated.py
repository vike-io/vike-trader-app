"""Verify the 5 migrated Studio strategy templates load and backtest via the unified Strategy API.

Each template subclasses Strategy (not SingleSymbolStrategy), uses symbol-explicit
on_bar verbs, and must complete a single-symbol portfolio backtest without error.
"""

import math

import pytest

from vike_trader_app.analysis.strategy_templates import TEMPLATES, StrategyTemplate
from vike_trader_app.core.model import Bar
from vike_trader_app.core.multi_symbol_engine import MultiSymbolEngine
from vike_trader_app.core.strategy import Strategy
from vike_trader_app.core.strategy_loader import load_strategy_from_string


def _bars(n=200):
    """Synthetic bars with trend + oscillation so the templates actually generate trades."""
    out = []
    prev = 100.0
    for i in range(n):
        p = 100.0 + 12.0 * math.sin(i / 9.0) + i * 0.05
        out.append(Bar(ts=i * 60_000, open=prev, high=max(p, prev) + 0.5,
                       low=min(p, prev) - 0.5, close=p, volume=1000.0))
        prev = p
    return out


# ---------------------------------------------------------------------------
# Each template: load, verify base class, run, assert final_equity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.name)
def test_template_loads_as_unified_strategy(template: StrategyTemplate):
    """Template code loads as a subclass of the unified Strategy (not the compat shim)."""
    cls = load_strategy_from_string(template.code, validate=True)
    assert issubclass(cls, Strategy), (
        f"{template.name}: expected a Strategy subclass, got {cls.__bases__}"
    )


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.name)
def test_template_completes_single_symbol_backtest(template: StrategyTemplate):
    """Template runs on a single-symbol MultiSymbolEngine and returns a float final_equity."""
    cls = load_strategy_from_string(template.code, validate=True)
    bars = _bars()
    result = MultiSymbolEngine(
        {"SYM": bars}, cls(), fee_rate=0.0, cash=10_000.0,
    ).run()
    assert isinstance(result.final_equity, float)
    assert result.final_equity > 0.0, f"{template.name}: equity went to zero or negative"
    assert len(result.equity_curve) == len(bars)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.name)
def test_template_preserves_warmup(template: StrategyTemplate):
    """Every template declares a non-negative integer WARMUP (preserved by migration)."""
    cls = load_strategy_from_string(template.code, validate=True)
    assert isinstance(cls.WARMUP, int) and cls.WARMUP >= 0, (
        f"{template.name}: WARMUP must be a non-negative int, got {cls.WARMUP!r}"
    )


def test_ma_crossover_defines_param_grid():
    """Only MaCrossover declares its own PARAM_GRID; the migration must preserve it non-empty.

    The other four templates don't define a PARAM_GRID — they inherit the empty
    base ``Strategy.PARAM_GRID = {}`` — so asserting a non-empty grid for all five
    would be misleading. We assert the non-empty grid only for the one that owns it.
    """
    ma = next(t for t in TEMPLATES if t.name == "MA crossover")
    cls = load_strategy_from_string(ma.code, validate=True)
    assert cls.PARAM_GRID == {"fast": [5, 10], "slow": [20, 30]}
