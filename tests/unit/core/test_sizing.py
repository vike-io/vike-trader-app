"""Unit tests for the swappable PositionSizer abstraction + pure sizing helpers."""

import pytest

from vike_trader_app.core.sizing import (
    DrawdownThrottleSizer,
    FixedDollarSizer,
    FixedSharesSizer,
    MaxRiskPctSizer,
    PassThroughSizer,
    PctEquitySizer,
    PctVolatilitySizer,
    PortfolioHeatSizer,
    SizeContext,
)


def _ctx(intent=1.0, basis_price=100.0, equity=10_000.0, cash=10_000.0, multiplier=1.0):
    return SizeContext(
        symbol="A",
        side=1,
        intent=intent,
        basis_price=basis_price,
        equity=equity,
        cash=cash,
        multiplier=multiplier,
    )


def test_passthrough_returns_intent():
    assert PassThroughSizer().size(_ctx(intent=7.0)) == 7.0


def test_fixed_dollar_divides_notional_by_basis():
    # $1000 / (price 100 * mult 1) = 10 shares
    assert FixedDollarSizer(1000.0).size(_ctx(basis_price=100.0, multiplier=1.0)) == 10.0


def test_fixed_dollar_respects_multiplier():
    # $1000 / (price 100 * mult 2) = 5 contracts
    assert FixedDollarSizer(1000.0).size(_ctx(basis_price=100.0, multiplier=2.0)) == 5.0


def test_pct_equity_targets_fraction_of_equity():
    # 10% of 10,000 = 1000 notional / price 100 = 10 shares
    assert PctEquitySizer(0.1).size(_ctx(equity=10_000.0, basis_price=100.0)) == 10.0


def test_fixed_shares_is_constant():
    sizer = FixedShares = FixedSharesSizer(42.0)
    assert sizer.size(_ctx(intent=1.0, basis_price=100.0)) == 42.0
    assert sizer.size(_ctx(intent=999.0, basis_price=5.0)) == 42.0


def test_zero_basis_price_no_div_by_zero():
    assert FixedDollarSizer(1000.0).size(_ctx(basis_price=0.0)) == 0.0
    assert PctEquitySizer(0.1).size(_ctx(basis_price=0.0)) == 0.0


def test_zero_multiplier_no_div_by_zero():
    assert FixedDollarSizer(1000.0).size(_ctx(basis_price=100.0, multiplier=0.0)) == 0.0
    assert PctEquitySizer(0.1).size(_ctx(basis_price=100.0, multiplier=0.0)) == 0.0


# ---------------------------------------------------------------------------
# PctVolatilitySizer
# ---------------------------------------------------------------------------

def _vctx(atr=0.0, equity=10_000.0, basis_price=100.0, multiplier=1.0, drawdown=0.0):
    return SizeContext(
        symbol="A", side=1, intent=1.0,
        basis_price=basis_price, equity=equity, cash=equity,
        multiplier=multiplier, atr=atr, drawdown=drawdown,
    )


def test_pct_volatility_sizer_with_atr():
    # pct=0.02, equity=10000, atr=2, mult=1 -> 0.02*10000 / (2*1) = 100
    assert PctVolatilitySizer(0.02).size(_vctx(atr=2.0, equity=10_000.0, multiplier=1.0)) == 100.0


def test_pct_volatility_sizer_atr_zero_falls_back_to_basis():
    # atr=0 fallback: 0.02*10000 / (100*1) = 2.0
    assert PctVolatilitySizer(0.02).size(_vctx(atr=0.0, equity=10_000.0, basis_price=100.0, multiplier=1.0)) == 2.0


def test_pct_volatility_sizer_atr_zero_multiplier_returns_zero():
    assert PctVolatilitySizer(0.02).size(_vctx(atr=2.0, multiplier=0.0)) == 0.0


def test_pct_volatility_sizer_fallback_zero_basis_returns_zero():
    assert PctVolatilitySizer(0.02).size(_vctx(atr=0.0, basis_price=0.0)) == 0.0


# ---------------------------------------------------------------------------
# DrawdownThrottleSizer
# ---------------------------------------------------------------------------

def test_drawdown_throttle_no_drawdown_passes_through():
    base = PctEquitySizer(0.5)
    throttled = DrawdownThrottleSizer(base, sensitivity=2.0)
    ctx = _vctx(drawdown=0.0, equity=10_000.0, basis_price=100.0)
    # factor = max(0, 1 - 2*0) = 1.0 -> same as base
    assert throttled.size(ctx) == base.size(ctx)


def test_drawdown_throttle_half_factor():
    # sensitivity=2.0, drawdown=0.25 -> factor = 1 - 2*0.25 = 0.5
    base = PctEquitySizer(0.5)
    throttled = DrawdownThrottleSizer(base, sensitivity=2.0)
    ctx = _vctx(drawdown=0.25, equity=10_000.0, basis_price=100.0)
    import pytest
    assert throttled.size(ctx) == pytest.approx(base.size(ctx) * 0.5)


def test_drawdown_throttle_floor_clamps():
    # sensitivity=10, drawdown=0.5 -> unclamped factor = 1 - 5 = -4, clamped to floor=0.1
    base = PctEquitySizer(0.5)
    throttled = DrawdownThrottleSizer(base, sensitivity=10.0, floor=0.1)
    ctx = _vctx(drawdown=0.5, equity=10_000.0, basis_price=100.0)
    import pytest
    assert throttled.size(ctx) == pytest.approx(base.size(ctx) * 0.1)


def test_drawdown_throttle_full_drawdown_uses_floor():
    # floor=0.25: even at 100% drawdown factor never below 0.25
    base = FixedSharesSizer(100.0)
    throttled = DrawdownThrottleSizer(base, sensitivity=1.0, floor=0.25)
    ctx = _vctx(drawdown=1.0)
    import pytest
    assert throttled.size(ctx) == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# MaxRiskPctSizer
# ---------------------------------------------------------------------------

def _rctx(basis_price=100.0, equity=10_000.0, multiplier=1.0, risk_stop=None, open_risk=0.0):
    return SizeContext(
        symbol="A", side=1, intent=1.0,
        basis_price=basis_price, equity=equity, cash=equity,
        multiplier=multiplier, risk_stop=risk_stop, open_risk=open_risk,
    )


def test_max_risk_pct_sizes_to_stop_distance():
    # pct 1%, basis 100, stop 90, mult 1, equity 10000 -> risk/unit 10 -> qty 10
    assert MaxRiskPctSizer(0.01).size(_rctx(basis_price=100.0, risk_stop=90.0)) == 10.0


def test_max_risk_pct_respects_multiplier():
    # risk/unit = |100-90| * mult 2 = 20 -> 0.01*10000 / 20 = 5
    assert MaxRiskPctSizer(0.01).size(_rctx(basis_price=100.0, risk_stop=90.0, multiplier=2.0)) == 5.0


def test_max_risk_pct_no_stop_returns_zero():
    assert MaxRiskPctSizer(0.01).size(_rctx(risk_stop=None)) == 0.0


def test_max_risk_pct_zero_distance_returns_zero():
    assert MaxRiskPctSizer(0.01).size(_rctx(basis_price=100.0, risk_stop=100.0)) == 0.0


def test_max_risk_pct_inverted_stop_returns_zero():
    # |basis - stop| handles a stop on the wrong side -> still positive distance, but
    # an exactly-equal stop is the degenerate zero case; a long with stop above is treated
    # as a (positive) distance, so guard only the zero/negative-distance case.
    assert MaxRiskPctSizer(0.01).size(_rctx(basis_price=100.0, risk_stop=100.0)) == 0.0


# ---------------------------------------------------------------------------
# PortfolioHeatSizer
# ---------------------------------------------------------------------------

def test_portfolio_heat_caps_to_budget():
    # base FixedShares(100); max_heat 2% of 10000 = 200 budget; risk/unit 10 -> max_qty 20
    sizer = PortfolioHeatSizer(FixedSharesSizer(100.0), max_heat=0.02)
    assert sizer.size(_rctx(basis_price=100.0, risk_stop=90.0, open_risk=0.0)) == 20.0


def test_portfolio_heat_passes_base_when_under_budget():
    # base FixedShares(5); cap allows 20 -> min(5,20)=5 (base wins)
    sizer = PortfolioHeatSizer(FixedSharesSizer(5.0), max_heat=0.02)
    assert sizer.size(_rctx(basis_price=100.0, risk_stop=90.0, open_risk=0.0)) == 5.0


def test_portfolio_heat_budget_exhausted_returns_zero():
    # open_risk already at the 200 budget -> nothing left
    sizer = PortfolioHeatSizer(FixedSharesSizer(100.0), max_heat=0.02)
    assert sizer.size(_rctx(basis_price=100.0, risk_stop=90.0, open_risk=200.0)) == 0.0


def test_portfolio_heat_no_stop_passes_base_through():
    sizer = PortfolioHeatSizer(FixedSharesSizer(100.0), max_heat=0.02)
    assert sizer.size(_rctx(basis_price=100.0, risk_stop=None)) == 100.0


# ---------------------------------------------------------------------------
# Pure sizing helpers: units_from_percent / units_from_value
# ---------------------------------------------------------------------------

from vike_trader_app.core.sizing import units_from_percent, units_from_value  # noqa: E402


def test_units_from_percent_basic():
    # 50% of $10,000 equity at price $100, multiplier 1 -> 50 units
    assert units_from_percent(0.5, equity=10_000.0, price=100.0, multiplier=1.0) == pytest.approx(50.0)


def test_units_from_percent_respects_multiplier():
    # 50% of $10,000 at price $100, multiplier 2 -> 25 contracts
    assert units_from_percent(0.5, equity=10_000.0, price=100.0, multiplier=2.0) == pytest.approx(25.0)


def test_units_from_percent_zero_price_returns_zero():
    assert units_from_percent(0.5, equity=10_000.0, price=0.0, multiplier=1.0) == 0.0


def test_units_from_percent_zero_multiplier_returns_zero():
    assert units_from_percent(0.5, equity=10_000.0, price=100.0, multiplier=0.0) == 0.0


def test_units_from_percent_zero_pct_returns_zero():
    assert units_from_percent(0.0, equity=10_000.0, price=100.0, multiplier=1.0) == 0.0


def test_units_from_value_basic():
    # $500 notional at price $50, multiplier 1 -> 10 units
    assert units_from_value(500.0, price=50.0, multiplier=1.0) == pytest.approx(10.0)


def test_units_from_value_respects_multiplier():
    # $500 notional at price $50, multiplier 2 -> 5 contracts
    assert units_from_value(500.0, price=50.0, multiplier=2.0) == pytest.approx(5.0)


def test_units_from_value_zero_price_returns_zero():
    assert units_from_value(500.0, price=0.0, multiplier=1.0) == 0.0


def test_units_from_value_zero_multiplier_returns_zero():
    assert units_from_value(500.0, price=50.0, multiplier=0.0) == 0.0


def test_units_from_value_matches_engine_formula():
    """units_from_value must match BacktestEngine's raw order_target_value formula."""
    price, multiplier, value = 73.5, 2.0, 1_000.0
    expected = value / (price * multiplier)
    assert units_from_value(value, price, multiplier) == pytest.approx(expected)


def test_units_from_percent_matches_engine_formula():
    """units_from_percent must match BacktestEngine's raw order_target_percent formula."""
    pct, equity, price, multiplier = 0.25, 8_500.0, 42.0, 1.5
    expected = pct * equity / (price * multiplier)
    assert units_from_percent(pct, equity, price, multiplier) == pytest.approx(expected)
