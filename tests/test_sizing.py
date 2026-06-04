"""Unit tests for the swappable PositionSizer abstraction."""

from vike_trader_app.core.sizing import (
    DrawdownThrottleSizer,
    FixedDollarSizer,
    FixedSharesSizer,
    PassThroughSizer,
    PctEquitySizer,
    PctVolatilitySizer,
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
