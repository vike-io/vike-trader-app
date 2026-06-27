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


def test_reduce_only_overshoot_rejected_when_qty_exceeds_position():
    gate = RiskGate(RiskLimits(block_reduce_only_overshoot=True))
    # long 2; a reduce_only sell of 3 would flip -> reject
    v = gate.check(_req(side=-1, qty=3.0, reduce_only=True), _ctx(position_size=2.0))
    assert v.ok is False and v.reason == "reduce-only-overshoot"


def test_reduce_only_within_position_passes():
    gate = RiskGate(RiskLimits(block_reduce_only_overshoot=True))
    v = gate.check(_req(side=-1, qty=2.0, reduce_only=True), _ctx(position_size=2.0))
    assert v.ok is True


def test_reduce_only_overshoot_flag_off_is_passthrough():
    # default RiskLimits() leaves the flag False -> paper/spot behavior unchanged
    gate = RiskGate(RiskLimits())
    v = gate.check(_req(side=-1, qty=3.0, reduce_only=True), _ctx(position_size=2.0))
    assert v.ok is True


def test_okx_swap_base_unit_qty_exposure_cap_in_quote_notional():
    """OKX SWAP gate is fed base-unit qty (step_size * ct_val already applied by app.py:gate_lot_size)
    and a base mark price. The exposure cap must trigger at the right quote-notional and must NOT
    be off by a factor of ct_val (which would make the cap meaninglessly large)."""
    # Example: BTC-USD-SWAP, ct_val=0.01 BTC, step_size=1 lot
    # => gate_lot_size = 1 * 0.01 = 0.01 BTC per lot (base-unit qty)
    # mark = 60000 USDT/BTC; buying 10 lots = 0.10 BTC = 6000 USDT notional
    # cap at 5000 USDT => should REJECT (6000 > 5000)
    gate = RiskGate(RiskLimits(max_total_exposure=5000.0, lot_size=0.01))
    # qty already in BASE units (0.10 BTC = 10 lots * 0.01 BTC/lot)
    v = gate.check(_req(side=1, qty=0.10), _ctx(position_size=0.0, mark_price=60000.0))
    assert v.ok is False, "exposure 0.10 * 60000 = 6000 must exceed cap of 5000"
    # 4 lots = 0.04 BTC = 2400 USDT notional => must PASS
    v2 = gate.check(_req(side=1, qty=0.04), _ctx(position_size=0.0, mark_price=60000.0))
    assert v2.ok is True, "exposure 0.04 * 60000 = 2400 must be under cap of 5000"
    # Verify: cap triggers at consistent notional across spot (no ct_val) and OKX perp
    # A spot order for 0.10 BTC at 60000 must ALSO be rejected by the same gate
    v3 = gate.check(_req(side=1, qty=0.10), _ctx(position_size=0.0, mark_price=60000.0))
    assert v3.ok is False, "same gate rejects spot 0.10@60000 identically (no ct_val scaling)"
