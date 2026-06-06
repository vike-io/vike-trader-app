"""Trade/equity statistics added for the standardized TesterReport."""

import pytest

from vike_trader_app.analysis.metrics import (
    net_profit, gross_profit, gross_loss, total_fees, expected_payoff,
    recovery_factor, consecutive_wins, consecutive_losses,
    largest_win, largest_loss, avg_win, avg_loss,
)
from vike_trader_app.core.model import Trade


def _t(pnl, fees=0.0):
    return Trade(entry_price=100.0, exit_price=100.0 + pnl, size=1.0, pnl=pnl, fees=fees)


def test_profit_aggregates():
    trades = [_t(10.0, 1.0), _t(-4.0, 1.0), _t(6.0, 1.0)]
    assert net_profit(trades) == 12.0
    assert gross_profit(trades) == 16.0
    assert gross_loss(trades) == 4.0
    assert total_fees(trades) == 3.0
    assert expected_payoff(trades) == 4.0


def test_empty_trades_are_zero_not_error():
    assert net_profit([]) == 0.0
    assert expected_payoff([]) == 0.0
    assert gross_loss([]) == 0.0
    assert largest_win([]) == 0.0
    assert avg_loss([]) == 0.0
    assert consecutive_wins([]) == 0


def test_recovery_factor_is_return_over_drawdown():
    eq = [100.0, 120.0, 110.0, 130.0]
    rf = recovery_factor(eq)
    assert rf == pytest.approx(0.30 / ((120.0 - 110.0) / 120.0))
    assert recovery_factor([100.0, 110.0, 120.0]) == float("inf")


def test_consecutive_runs():
    trades = [_t(1), _t(2), _t(-1), _t(3), _t(4), _t(5), _t(-1), _t(-2)]
    assert consecutive_wins(trades) == 3
    assert consecutive_losses(trades) == 2


def test_win_loss_extremes_and_means():
    trades = [_t(10), _t(-4), _t(6), _t(-2)]
    assert largest_win(trades) == 10.0
    assert largest_loss(trades) == -4.0
    assert avg_win(trades) == 8.0
    assert avg_loss(trades) == -3.0
