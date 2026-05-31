"""Canonical cost primitives — the single source of truth shared by engine + kernel."""

from vike_trader_app.core.broker_sim import adverse_fill_price, fee, funding_charge


def test_adverse_fill_price_moves_against_the_taker():
    # a buy (+1) fills higher, a sell (-1) fills lower, by the slippage fraction
    assert adverse_fill_price(100.0, +1, 0.001) == 100.1
    assert adverse_fill_price(100.0, -1, 0.001) == 99.9
    assert adverse_fill_price(100.0, +1, 0.0) == 100.0


def test_fee_is_rate_on_notional_times_multiplier():
    assert fee(2.0, 100.0, 0.001, 1.0) == 0.2
    assert fee(2.0, 100.0, 0.001, 5.0) == 1.0   # multiplier scales the notional
    assert fee(1.0, 50.0, 0.0, 1.0) == 0.0


def test_funding_charge_signed_by_position_times_multiplier():
    # longs (pos>0) pay positive funding; charge = pos*price*rate*mult
    assert funding_charge(3.0, 100.0, 0.0001, 1.0) == 3.0 * 100.0 * 0.0001
    assert funding_charge(-3.0, 100.0, 0.0001, 1.0) == -3.0 * 100.0 * 0.0001  # shorts receive
    assert funding_charge(3.0, 100.0, 0.0001, 5.0) == 3.0 * 100.0 * 0.0001 * 5.0
