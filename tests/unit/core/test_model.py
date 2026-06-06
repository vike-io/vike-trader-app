"""Domain-model behavior tests (Phase 1, step 1)."""

from vike_trader_app.core.model import Position


def test_long_position_unrealized_pnl():
    pos = Position(size=2.0, avg_price=100.0)
    assert pos.unrealized_pnl(110.0) == 20.0


def test_short_position_unrealized_pnl():
    pos = Position(size=-2.0, avg_price=100.0)
    assert pos.unrealized_pnl(90.0) == 20.0


def test_flat_position_has_zero_pnl():
    pos = Position(size=0.0, avg_price=0.0)
    assert pos.unrealized_pnl(123.0) == 0.0
