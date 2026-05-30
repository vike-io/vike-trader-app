"""Phase 3a analytics: risk-adjusted metrics + trade excursions (pure functions)."""

import math

import pytest

from vike_trader_app.analysis.metrics import sortino, calmar, omega
from vike_trader_app.analysis.excursions import mae_mfe, edge_ratio, expanding_trade_metrics
from vike_trader_app.core.model import Bar, Trade


# returns are exactly [+0.1, -0.1, +0.1]: 110/100, 99/110(=0.9), 108.9/99(=1.1)
EQUITY = [100.0, 110.0, 99.0, 108.9]


def test_sortino_uses_downside_deviation_only():
    # mean = 0.1/3; downside var = (-0.1)^2 / (3-1) = 0.005; dev = sqrt(0.005)
    # periods_per_year=1 -> annualization factor 1.0
    expected = (0.1 / 3) / math.sqrt(0.005)
    assert sortino(EQUITY, periods_per_year=1) == pytest.approx(expected, rel=1e-9)


def test_sortino_zero_when_no_downside():
    assert sortino([100.0, 101.0, 102.0], periods_per_year=1) == 0.0


def test_sortino_short_curve_is_zero():
    assert sortino([100.0], periods_per_year=1) == 0.0


def test_calmar_is_cagr_over_max_drawdown():
    # periods_per_year = 3 = number of returns -> annualization exponent 1 -> CAGR = total return
    # total return = 108.9/100 - 1 = 0.089; max_drawdown = (110-99)/110 = 0.1
    assert calmar(EQUITY, periods_per_year=3) == pytest.approx(0.089 / 0.1, rel=1e-9)


def test_calmar_inf_when_no_drawdown():
    assert calmar([100.0, 110.0, 120.0], periods_per_year=2) == float("inf")


def test_calmar_zero_for_short_curve():
    assert calmar([100.0], periods_per_year=1) == 0.0


def test_omega_gain_loss_ratio():
    # returns [+0.1,-0.1,+0.1], threshold 0: gains=0.2, losses=0.1 -> 2.0
    assert omega(EQUITY, threshold=0.0) == pytest.approx(2.0, rel=1e-9)


def test_omega_inf_when_no_losses():
    assert omega([100.0, 110.0, 121.0], threshold=0.0) == float("inf")


def test_omega_zero_when_no_gains():
    assert omega([100.0, 90.0, 81.0], threshold=0.0) == 0.0


def _window_bars(low_at_2, high_at_2):
    # 4 bars at ts 0,100,200,300; bar index 2 carries the extreme low/high
    return [
        Bar(ts=0, open=100.0, high=101.0, low=99.0, close=100.0),
        Bar(ts=100, open=100.0, high=102.0, low=98.0, close=101.0),
        Bar(ts=200, open=101.0, high=high_at_2, low=low_at_2, close=100.0),
        Bar(ts=300, open=100.0, high=101.0, low=99.0, close=100.0),
    ]


def test_mae_mfe_long():
    # long: entry 100, exit 110, profit on a rise -> direction +1
    trade = Trade(entry_price=100.0, exit_price=110.0, size=1.0, pnl=10.0, entry_ts=0, exit_ts=300)
    bars = _window_bars(low_at_2=95.0, high_at_2=115.0)
    mae, mfe = mae_mfe(trade, bars)
    assert mae == pytest.approx(0.05)   # (100-95)/100
    assert mfe == pytest.approx(0.15)   # (115-100)/100


def test_mae_mfe_short():
    # short: entry 100, exit 90, profit on a fall -> direction -1
    trade = Trade(entry_price=100.0, exit_price=90.0, size=1.0, pnl=10.0, entry_ts=0, exit_ts=300)
    bars = _window_bars(low_at_2=85.0, high_at_2=105.0)
    mae, mfe = mae_mfe(trade, bars)
    assert mae == pytest.approx(0.05)   # adverse = high: (105-100)/100
    assert mfe == pytest.approx(0.15)   # favorable = low: (100-85)/100


def test_mae_mfe_no_window_bars_is_zero():
    trade = Trade(entry_price=100.0, exit_price=110.0, size=1.0, pnl=10.0, entry_ts=1000, exit_ts=2000)
    bars = _window_bars(low_at_2=95.0, high_at_2=115.0)  # all ts < 1000
    assert mae_mfe(trade, bars) == (0.0, 0.0)


def test_edge_ratio_mean_mfe_over_mean_mae():
    # two identical long trades, each MAE 0.05 / MFE 0.15 -> edge 3.0
    bars = _window_bars(low_at_2=95.0, high_at_2=115.0)
    t = Trade(entry_price=100.0, exit_price=110.0, size=1.0, pnl=10.0, entry_ts=0, exit_ts=300)
    assert edge_ratio([t, t], bars) == pytest.approx(3.0, rel=1e-9)


def test_edge_ratio_empty_is_zero():
    assert edge_ratio([], []) == 0.0


def test_edge_ratio_inf_when_no_adverse():
    # a trade whose window never goes below entry -> mean MAE 0 -> inf
    bars = [Bar(ts=0, open=100.0, high=120.0, low=100.0, close=110.0),
            Bar(ts=100, open=110.0, high=130.0, low=105.0, close=120.0)]
    t = Trade(entry_price=100.0, exit_price=120.0, size=1.0, pnl=20.0, entry_ts=0, exit_ts=100)
    assert edge_ratio([t], bars) == float("inf")
