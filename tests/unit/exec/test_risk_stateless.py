"""RiskGate stateless per-order checks: rounding, min-notional, notional + exposure caps."""

from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.risk import RiskContext, RiskGate, RiskLimits, RiskVerdict


def _req(side=+1, qty=1.0, price=100.0, order_type="limit", reduce_only=False):
    return OrderRequest(client_order_id="c", venue="binance", symbol="BTCUSDT",
                        side=side, qty=qty, order_type=order_type, price=price,
                        reduce_only=reduce_only)


def _ctx(position_size=0.0, mark_price=100.0):
    return RiskContext(position_size=position_size, mark_price=mark_price)


def test_accepts_a_clean_order_unchanged():
    gate = RiskGate(RiskLimits())
    v = gate.check(_req(), _ctx())
    assert v.ok and v.reason == "" and v.request.qty == 1.0 and v.request.price == 100.0


def test_rounds_price_to_tick_and_size_to_lot():
    gate = RiskGate(RiskLimits(tick_size=0.5, lot_size=0.01))
    v = gate.check(_req(qty=1.237, price=100.27), _ctx())
    assert v.ok
    assert v.request.price == 100.5      # round to nearest 0.5
    assert v.request.qty == 1.24         # round to nearest 0.01


def test_rejects_non_positive_size():
    gate = RiskGate(RiskLimits())
    assert gate.check(_req(qty=0.0), _ctx()).ok is False
    v = gate.check(_req(qty=-1.0), _ctx())
    assert v.ok is False and v.reason == "non-positive-size"


def test_rejects_below_min_notional():
    gate = RiskGate(RiskLimits(min_notional=10.0))
    # qty 0.05 * price 100 = 5.0 < 10 -> reject
    v = gate.check(_req(qty=0.05, price=100.0), _ctx())
    assert v.ok is False and v.reason == "below-min-notional"
    assert gate.check(_req(qty=0.2, price=100.0), _ctx()).ok is True  # 20 >= 10


def test_market_order_uses_mark_price_for_notional():
    gate = RiskGate(RiskLimits(min_notional=10.0))
    # market order has price=None; notional uses ctx.mark_price
    v = gate.check(_req(price=None, order_type="market", qty=0.05), _ctx(mark_price=100.0))
    assert v.ok is False and v.reason == "below-min-notional"


def test_rejects_over_max_notional_per_order():
    gate = RiskGate(RiskLimits(max_notional_per_order=1000.0))
    v = gate.check(_req(qty=11.0, price=100.0), _ctx())  # 1100 > 1000
    assert v.ok is False and v.reason == "over-max-notional"
    assert gate.check(_req(qty=9.0, price=100.0), _ctx()).ok is True  # 900


def test_rejects_over_max_total_exposure_on_projected_position():
    gate = RiskGate(RiskLimits(max_total_exposure=1500.0))
    # already long 10 @ mark 100 (= 1000 exposure); buy 6 more -> projected 16*100 = 1600 > 1500
    v = gate.check(_req(side=+1, qty=6.0, price=100.0), _ctx(position_size=10.0, mark_price=100.0))
    assert v.ok is False and v.reason == "over-max-exposure"
    # a reducing order shrinks projected exposure -> allowed
    assert gate.check(_req(side=-1, qty=6.0, price=100.0),
                      _ctx(position_size=10.0, mark_price=100.0)).ok is True


def test_verdict_carries_the_normalized_request():
    gate = RiskGate(RiskLimits(tick_size=0.1))
    v = gate.check(_req(price=100.04), _ctx())
    assert isinstance(v, RiskVerdict) and v.request.price == 100.0  # rounded copy, original untouched


def test_rejects_invalid_side():
    gate = RiskGate(RiskLimits())
    for bad in (0, 2, -2):
        v = gate.check(_req(side=bad), _ctx())
        assert v.ok is False and v.reason == "invalid-side"


def test_stop_order_notional_uses_trigger_price_not_mark():
    # stop far from mark: per-order notional must be sized off the trigger, not the mark
    gate = RiskGate(RiskLimits(max_notional_per_order=1000.0))
    req = OrderRequest(client_order_id="c", venue="binance", symbol="BTCUSDT",
                       side=+1, qty=6.0, order_type="stop", price=None, trigger_price=200.0)
    # at mark (100*6=600) it would pass; at trigger (200*6=1200) it must be denied
    v = gate.check(req, RiskContext(mark_price=100.0))
    assert v.ok is False and v.reason == "over-max-notional"
