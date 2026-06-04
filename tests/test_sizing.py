"""Unit tests for the swappable PositionSizer abstraction."""

from vike_trader_app.core.sizing import (
    FixedDollarSizer,
    FixedSharesSizer,
    PassThroughSizer,
    PctEquitySizer,
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
