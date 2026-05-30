"""Phase 3a analytics: risk-adjusted metrics + trade excursions (pure functions)."""

import math

import pytest

from vike_trader_app.analysis.metrics import sortino, calmar, omega
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
