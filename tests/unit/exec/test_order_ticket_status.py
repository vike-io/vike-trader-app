from vike_trader_app.exec.events import (
    FillEvent, OrderAccepted, OrderDenied, OrderFilled, OrderPartiallyFilled,
    OrderRejected, OrderSubmitted,
)
from vike_trader_app.exec.order_ticket import OrderTicketStatus


def _armed(coid="c1"):
    s = OrderTicketStatus()
    s.arm(coid)
    return s


def test_ignores_events_for_other_orders():
    s = _armed("mine")
    assert s.on_event(OrderAccepted(client_order_id="someone-else")) is None


def test_submitted_then_accepted():
    s = _armed()
    assert s.on_event(OrderSubmitted(client_order_id="c1")) == "sent"
    assert s.on_event(OrderAccepted(client_order_id="c1")) == "accepted"


def test_denied_shows_reason_uppercase_marker():
    s = _armed()
    out = s.on_event(OrderDenied(client_order_id="c1", reason="max-notional"))
    assert out == "DENIED: max-notional"


def test_rejected_is_distinct_from_denied():
    s = _armed()
    out = s.on_event(OrderRejected(client_order_id="c1", reason="insufficient-balance"))
    assert out == "REJECTED: insufficient-balance"


def test_partial_fill_shows_partial():
    # OrderPartiallyFilled has a REQUIRED fill: FillEvent field (no default) — must pass a real one.
    # (verified: exec/events.py:99-102; the 'partial' status is bare — no cumulative f/q on the wrap.)
    s = _armed()
    fe = FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol="BTCUSDT",
                   side=1, last_qty=0.005, last_px=65000.0)
    out = s.on_event(OrderPartiallyFilled(client_order_id="c1", fill=fe))
    assert out == "partial"


def test_filled():
    # OrderFilled also has a REQUIRED fill: FillEvent field (no default).
    # (verified: exec/events.py:105-108)
    s = _armed()
    fe = FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol="BTCUSDT",
                   side=1, last_qty=0.01, last_px=65000.0)
    out = s.on_event(OrderFilled(client_order_id="c1", fill=fe))
    assert out == "filled"


def test_fill_event_for_armed_order_renders_qty_and_px():
    s = _armed()
    fe = FillEvent(trade_id="t1", client_order_id="c1", venue="binance", symbol="BTCUSDT",
                   side=1, last_qty=0.01, last_px=65000.0)
    out = s.on_event(fe)
    assert "0.01" in out and "65000" in out


def test_fill_event_for_other_order_ignored():
    s = _armed("c1")
    fe = FillEvent(trade_id="t1", client_order_id="other", venue="binance", symbol="BTCUSDT",
                   side=1, last_qty=0.01, last_px=65000.0)
    assert s.on_event(fe) is None


def test_arm_resets_tracking():
    s = _armed("c1")
    s.arm("c2")
    assert s.on_event(OrderAccepted(client_order_id="c1")) is None
    assert s.on_event(OrderAccepted(client_order_id="c2")) == "accepted"
