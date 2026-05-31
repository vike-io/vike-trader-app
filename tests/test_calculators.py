"""Unit tests for the standalone Tools-tab calculators (pure math)."""

import math

from vike_trader_app.analysis import calculators as C


def test_position_size():
    r = C.position_size(account=10_000.0, risk_pct=1.0, entry=100.0, stop=95.0)
    assert r["risk_amount"] == 100.0
    assert r["risk_per_unit"] == 5.0
    assert r["qty"] == 20.0
    assert r["notional"] == 2_000.0


def test_position_size_zero_distance_is_safe():
    r = C.position_size(10_000.0, 1.0, 100.0, 100.0)
    assert r["qty"] == 0.0  # no division by zero when entry == stop


def test_liquidation_price_long_and_short():
    assert C.liquidation_price(100.0, 10.0, "long", 0.5) == 100.0 * (1 - 0.1 + 0.005)
    assert C.liquidation_price(100.0, 10.0, "short", 0.5) == 100.0 * (1 + 0.1 - 0.005)
    assert C.liquidation_price(100.0, 0.0) == 0.0  # invalid leverage


def test_funding_cost():
    assert C.funding_cost(10_000.0, 0.0001, 3) == 3.0


def test_trade_pnl_long():
    r = C.trade_pnl(entry=100.0, exit_=110.0, qty=10.0, side="long", fee_rate=0.001)
    assert r["gross"] == 100.0
    assert math.isclose(r["fees"], (100.0 + 110.0) * 10.0 * 0.001)
    assert math.isclose(r["net"], 100.0 - r["fees"])
    assert math.isclose(r["return_pct"], r["net"] / 1_000.0 * 100.0)


def test_trade_pnl_short():
    r = C.trade_pnl(entry=110.0, exit_=100.0, qty=10.0, side="short")
    assert r["gross"] == 100.0  # short profits when price falls


def test_expectancy():
    r = C.expectancy(win_rate=0.5, avg_win=2.0, avg_loss=1.0)
    assert r["expectancy"] == 0.5
    assert r["profit_factor"] == 2.0


def test_risk_of_ruin_is_bounded_and_edge_sensitive():
    losing = C.risk_of_ruin(0.30, 1.0, 5.0, seed=1, trials=400, max_trades=400)
    winning = C.risk_of_ruin(0.60, 1.5, 5.0, seed=1, trials=400, max_trades=400)
    assert 0.0 <= winning <= 1.0 and 0.0 <= losing <= 1.0
    assert losing > winning  # a negative edge ruins far more often than a positive one


def test_risk_of_ruin_deterministic_with_seed():
    a = C.risk_of_ruin(0.45, 1.2, 3.0, seed=42, trials=300, max_trades=300)
    b = C.risk_of_ruin(0.45, 1.2, 3.0, seed=42, trials=300, max_trades=300)
    assert a == b
