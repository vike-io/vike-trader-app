"""Performance-metric tests (Phase 1 stats panel + extended metrics)."""

import math
import pytest

from vike_trader_app.analysis.metrics import (
    cagr,
    exposure,
    k_ratio,
    mar_ratio,
    max_drawdown,
    payoff_ratio,
    profit_factor,
    sharpe,
    total_return,
    ulcer_index,
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


# ---------------------------------------------------------------------------
# Extended metrics
# ---------------------------------------------------------------------------

# --- cagr() ---

def test_cagr_rising_curve_positive():
    eq = [10_000.0 * (1.001 ** i) for i in range(252)]
    assert cagr(eq, periods_per_year=252) > 0


def test_cagr_flat_is_zero():
    assert cagr([100.0] * 10, periods_per_year=252) == pytest.approx(0.0, abs=1e-12)


def test_cagr_empty_is_zero():
    assert cagr([], periods_per_year=252) == 0.0


def test_cagr_single_bar_is_zero():
    assert cagr([100.0], periods_per_year=252) == 0.0


def test_cagr_known_value():
    # Double equity over 252 steps → CAGR = 2^(252/251) - 1 ≈ 1 period exponent
    eq = [10_000.0, 20_000.0]
    expected = (20_000.0 / 10_000.0) ** 252 - 1.0
    assert cagr(eq, periods_per_year=252) == pytest.approx(expected)


# --- ulcer_index() ---

def test_ulcer_index_monotone_up_is_zero():
    eq = [100.0 + i for i in range(20)]
    assert ulcer_index(eq) == pytest.approx(0.0)


def test_ulcer_index_positive_for_curve_with_drawdowns():
    eq = [100.0, 120.0, 90.0, 110.0]
    assert ulcer_index(eq) > 0


def test_ulcer_index_short_curve():
    assert ulcer_index([100.0]) == 0.0


def test_ulcer_index_empty():
    assert ulcer_index([]) == 0.0


def test_ulcer_index_deeper_drawdown_gives_higher_value():
    eq_shallow = [100.0, 110.0, 105.0, 115.0]
    eq_deep = [100.0, 110.0, 50.0, 115.0]
    assert ulcer_index(eq_deep) > ulcer_index(eq_shallow)


# --- mar_ratio() ---

def test_mar_ratio_positive_for_rising_curve():
    eq = [10_000.0 + i * 50 for i in range(252)]  # steady rise, some conceptual dd
    # With a truly monotone curve max_drawdown = 0 → inf
    assert mar_ratio(eq, periods_per_year=252) == float("inf")


def test_mar_ratio_zero_cagr_and_zero_dd():
    assert mar_ratio([100.0] * 10, periods_per_year=252) == 0.0


def test_mar_ratio_uses_cagr_over_mdd():
    # Construct a curve with known CAGR and drawdown
    eq = [10_000.0, 12_000.0, 9_000.0, 12_000.0]
    c = cagr(eq, periods_per_year=1)
    mdd = max_drawdown(eq)
    expected = c / mdd if mdd else float("inf")
    assert mar_ratio(eq, periods_per_year=1) == pytest.approx(expected)


# --- k_ratio() ---

def test_k_ratio_rising_curve_is_positive():
    eq = [10_000.0 * (1.001 ** i) for i in range(100)]
    assert k_ratio(eq) > 0


def test_k_ratio_flat_is_zero():
    assert k_ratio([100.0] * 10) == 0.0


def test_k_ratio_empty_is_zero():
    assert k_ratio([]) == 0.0


def test_k_ratio_single_bar_is_zero():
    assert k_ratio([100.0]) == 0.0


def test_k_ratio_two_bars_is_zero():
    # OLS regression on 2 points: no residual → se=0 guard should return 0
    assert k_ratio([100.0, 110.0]) == 0.0


def test_k_ratio_declining_curve_is_negative():
    eq = [10_000.0 * (0.999 ** i) for i in range(100)]
    assert k_ratio(eq) < 0


# --- payoff_ratio() ---

def test_payoff_ratio_known_value():
    trades = [_t(20), _t(10), _t(-5), _t(-5)]
    # avg_win = 15, avg_loss = -5 → payoff = 15/5 = 3
    assert payoff_ratio(trades) == pytest.approx(3.0)


def test_payoff_ratio_no_losses_is_zero():
    assert payoff_ratio([_t(10), _t(20)]) == 0.0


def test_payoff_ratio_no_wins_is_zero():
    assert payoff_ratio([_t(-10), _t(-5)]) == 0.0


def test_payoff_ratio_no_trades_is_zero():
    assert payoff_ratio([]) == 0.0


# --- exposure() ---

def test_exposure_all_active():
    eq = [100.0] * 5
    sizes = [1.0, 2.0, -1.0, 0.5, 1.0]
    assert exposure(eq, sizes) == pytest.approx(1.0)


def test_exposure_half_active():
    eq = [100.0] * 4
    sizes = [1.0, 0.0, 1.0, 0.0]
    assert exposure(eq, sizes) == pytest.approx(0.5)


def test_exposure_none_active():
    eq = [100.0] * 3
    sizes = [0.0, 0.0, 0.0]
    assert exposure(eq, sizes) == pytest.approx(0.0)


def test_exposure_empty_is_zero():
    assert exposure([], []) == 0.0


def test_exposure_length_mismatch_is_zero():
    assert exposure([100.0, 110.0], [1.0]) == 0.0
