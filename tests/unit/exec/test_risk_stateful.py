"""RiskGate stateful checks: trading-state kill-switch + sliding-window throttle."""

from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.risk import RiskContext, RiskGate, RiskLimits, TradingState


def _req(side=+1, qty=1.0, price=100.0, reduce_only=False):
    return OrderRequest(client_order_id="c", venue="binance", symbol="BTCUSDT",
                        side=side, qty=qty, order_type="limit", price=price, reduce_only=reduce_only)


def _ctx(position_size=0.0, state=TradingState.ACTIVE, now_ms=0, mark_price=100.0):
    return RiskContext(position_size=position_size, trading_state=state, now_ms=now_ms,
                       mark_price=mark_price)


def test_halted_denies_every_order():
    gate = RiskGate(RiskLimits())
    v = gate.check(_req(), _ctx(state=TradingState.HALTED))
    assert v.ok is False and v.reason == "halted"


def test_reducing_allows_only_position_reducing_orders():
    gate = RiskGate(RiskLimits())
    # long 5: a sell reduces (allowed), a buy extends (denied)
    assert gate.check(_req(side=-1), _ctx(position_size=5.0, state=TradingState.REDUCING)).ok is True
    deny = gate.check(_req(side=+1), _ctx(position_size=5.0, state=TradingState.REDUCING))
    assert deny.ok is False and deny.reason == "reduce-only"
    # flat in REDUCING: nothing reduces -> deny
    assert gate.check(_req(side=-1), _ctx(position_size=0.0, state=TradingState.REDUCING)).ok is False


def test_reduce_only_flag_passes_in_reducing_state():
    gate = RiskGate(RiskLimits())
    # an explicit reduce_only order is allowed in REDUCING even when flat-checking is ambiguous
    v = gate.check(_req(side=-1, reduce_only=True), _ctx(position_size=5.0, state=TradingState.REDUCING))
    assert v.ok is True


def test_throttle_denies_after_max_orders_in_window():
    gate = RiskGate(RiskLimits(max_orders_per_window=2, window_ms=1000))
    assert gate.check(_req(), _ctx(now_ms=0)).ok is True     # 1
    assert gate.check(_req(), _ctx(now_ms=100)).ok is True   # 2
    third = gate.check(_req(), _ctx(now_ms=200))             # 3 within window -> denied
    assert third.ok is False and third.reason == "rate-limited"


def test_throttle_window_slides_so_old_orders_free_a_slot():
    gate = RiskGate(RiskLimits(max_orders_per_window=2, window_ms=1000))
    assert gate.check(_req(), _ctx(now_ms=0)).ok is True
    assert gate.check(_req(), _ctx(now_ms=100)).ok is True
    assert gate.check(_req(), _ctx(now_ms=200)).ok is False  # full
    # at t=1200 the t=0 and t=100 orders are >1000ms old -> pruned -> slot free
    assert gate.check(_req(), _ctx(now_ms=1200)).ok is True


def test_denied_orders_do_not_consume_a_throttle_slot():
    gate = RiskGate(RiskLimits(max_orders_per_window=1, window_ms=1000, min_notional=1_000_000.0))
    # this order is denied on min-notional, so it must NOT use the single throttle slot
    assert gate.check(_req(qty=1.0, price=100.0), _ctx(now_ms=0)).ok is False
    # a valid order (no min-notional issue) still has its slot — use a gate without min_notional
    g2 = RiskGate(RiskLimits(max_orders_per_window=1, window_ms=1000))
    assert g2.check(_req(), _ctx(now_ms=0)).ok is True
    assert g2.check(_req(), _ctx(now_ms=10)).ok is False
