"""Test the Strategy event handler surface (Task 1: Fill + Nautilus-parity stubs)."""

from vike_trader_app.core.model import Fill, Position
from vike_trader_app.core.orders import Order
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy

_HOOKS = [
    "on_start",
    "on_stop",
    "on_order_submitted",
    "on_order_accepted",
    "on_order_rejected",
    "on_order_filled",
    "on_order_canceled",
    "on_order_expired",
    "on_order_updated",
    "on_position_opened",
    "on_position_changed",
    "on_position_closed",
    "on_event",
    "on_liquidation",
]


def test_fill_fields():
    """Fill has all required fields with defaults."""
    f = Fill(side=+1, size=2.0, price=10.0, fee=0.1, ts=5, is_maker=True, symbol="X")
    assert (f.side, f.size, f.price, f.fee, f.ts, f.is_maker, f.symbol) == (
        1,
        2.0,
        10.0,
        0.1,
        5,
        True,
        "X",
    )
    assert Fill(side=-1, size=1.0, price=9.0, fee=0.0, ts=0).symbol == ""


def test_all_hooks_exist_and_noop():
    """All handlers exist on Strategy and return None (no-op defaults)."""
    s = Strategy()
    assert s.on_start() is None and s.on_stop() is None
    assert s.on_order_filled(Fill(+1, 1.0, 10.0, 0.0, 0)) is None
    assert s.on_order_submitted(Order("market", +1, 1.0)) is None
    assert s.on_position_opened(Position(1.0, 10.0)) is None
    assert s.on_event(Fill(+1, 1.0, 10.0, 0.0, 0)) is None
    for h in _HOOKS:  # every hook is defined and callable
        assert callable(getattr(s, h))


def test_no_on_fill():
    """Pure Nautilus: no on_fill (only on_order_filled)."""
    assert not hasattr(Strategy(), "on_fill")


def test_hooks_overridable():
    """Handlers can be overridden in subclasses."""
    seen = []

    class S(Strategy):
        def on_order_filled(self, fill):
            seen.append(("filled", fill.price))

        def on_position_opened(self, position):
            seen.append(("opened", position.size))

    s = S()
    s.on_order_filled(Fill(+1, 1.0, 42.0, 0.0, 0))
    s.on_position_opened(Position(1.0, 42.0))
    assert seen == [("filled", 42.0), ("opened", 1.0)]
