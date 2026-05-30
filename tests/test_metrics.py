"""Performance-metric tests (Phase 1 stats panel)."""

import pytest

from vike_trader_app.analysis.metrics import (
    max_drawdown,
    profit_factor,
    sharpe,
    total_return,
    win_rate,
)
from vike_trader_app.core.model import Trade


def _t(pnl):
    return Trade(entry_price=1, exit_price=1, size=1, pnl=pnl)


def test_total_return():
    assert total_return([10_000, 10_020]) == pytest.approx(0.002)


def test_total_return_empty_is_zero():
    assert total_return([]) == 0.0


def test_win_rate():
    assert win_rate([_t(10), _t(-5), _t(3), _t(-1)]) == 0.5


def test_win_rate_no_trades_is_zero():
    assert win_rate([]) == 0.0


def test_max_drawdown():
    # running peak 120 -> trough 90 = 0.25
    assert max_drawdown([100, 120, 90, 110]) == pytest.approx(0.25)


def test_max_drawdown_monotonic_is_zero():
    assert max_drawdown([100, 110, 120]) == 0.0


def test_profit_factor():
    # gross profit 13, gross loss 6 -> ~2.1667
    assert profit_factor([_t(10), _t(3), _t(-5), _t(-1)]) == pytest.approx(13 / 6)


def test_profit_factor_no_losses_is_inf():
    assert profit_factor([_t(10), _t(5)]) == float("inf")


def test_sharpe_zero_without_variance():
    assert sharpe([100, 100, 100]) == 0.0


def test_sharpe_positive_for_net_gains():
    assert sharpe([100, 110, 105, 120], periods_per_year=1) > 0
