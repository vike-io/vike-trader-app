from vike_trader_app.exec.events import OrderRequest
from vike_trader_app.exec.order import ManagedOrder, OrderStatus
from vike_trader_app.exec.positions_view import (
    OrderRow, PanelRows, PositionRow, project_positions_orders,
)


class _FakeAccount:
    def __init__(self, positions, marks=None, balance=0.0, realized_pnl=0.0):
        self.positions = positions
        self.marks = marks or {}
        self.balance = balance
        self.realized_pnl = realized_pnl

    def unrealized_pnl(self, venue, symbol, position_side="BOTH"):
        pos = self.positions.get((venue, symbol, position_side))
        mark = self.marks.get((venue, symbol))
        if pos is None or mark is None:
            return 0.0
        return (mark - pos["avg_px"]) * pos["size"]


def _mo(coid, *, symbol="BTCUSDT", side=1, qty=0.01, order_type="limit",
        price=65000.0, status=OrderStatus.ACCEPTED, filled=0.0, avg=0.0):
    req = OrderRequest(client_order_id=coid, venue="binance", symbol=symbol,
                       side=side, qty=qty, order_type=order_type, price=price)
    return ManagedOrder(request=req, status=status, filled_qty=filled, avg_fill_px=avg)


def test_empty_account_and_registry_gives_empty_rows():
    rows = project_positions_orders(_FakeAccount({}), {}, "binance")
    assert rows == PanelRows()
    assert rows.positions == () and rows.orders == ()


def test_open_position_projected_with_upnl_and_mark():
    acct = _FakeAccount(
        positions={("binance", "BTCUSDT", "BOTH"): {"size": 0.01, "avg_px": 64000.0}},
        marks={("binance", "BTCUSDT"): 65000.0}, balance=1000.0, realized_pnl=12.5)
    rows = project_positions_orders(acct, {}, "binance")
    assert rows.balance == 1000.0
    assert rows.realized_pnl == 12.5
    assert len(rows.positions) == 1
    p = rows.positions[0]
    assert p == PositionRow("binance", "BTCUSDT", "BOTH", 0.01, 64000.0, 65000.0,
                            (65000.0 - 64000.0) * 0.01)


def test_flat_position_is_skipped():
    acct = _FakeAccount(positions={("binance", "BTCUSDT", "BOTH"): {"size": 0.0, "avg_px": 0.0}})
    assert project_positions_orders(acct, {}, "binance").positions == ()


def test_position_for_other_venue_is_skipped():
    acct = _FakeAccount(positions={("bybit", "BTCUSDT", "BOTH"): {"size": 0.01, "avg_px": 64000.0}})
    assert project_positions_orders(acct, {}, "binance").positions == ()


def test_hedge_long_and_short_legs_both_projected():
    acct = _FakeAccount(positions={
        ("binance", "BTCUSDT", "LONG"): {"size": 0.02, "avg_px": 64000.0},
        ("binance", "BTCUSDT", "SHORT"): {"size": -0.01, "avg_px": 66000.0},
    })
    rows = project_positions_orders(acct, {}, "binance")
    sides = sorted(p.position_side for p in rows.positions)
    assert sides == ["LONG", "SHORT"]


def test_mark_absent_gives_none_mark_and_zero_upnl():
    acct = _FakeAccount(positions={("binance", "BTCUSDT", "BOTH"): {"size": 0.01, "avg_px": 64000.0}})
    p = project_positions_orders(acct, {}, "binance").positions[0]
    assert p.mark is None
    assert p.unrealized_pnl == 0.0


def test_open_order_projected():
    reg = {"c1": _mo("c1", filled=0.004, avg=64900.0)}
    rows = project_positions_orders(_FakeAccount({}), reg, "binance")
    assert rows.orders == (
        OrderRow("c1", "BTCUSDT", 1, 0.01, "limit", "ACCEPTED", 0.004, 64900.0, 65000.0),)


def test_terminal_orders_excluded():
    reg = {
        "live": _mo("live", status=OrderStatus.ACCEPTED),
        "part": _mo("part", status=OrderStatus.PARTIALLY_FILLED),
        "filled": _mo("filled", status=OrderStatus.FILLED),
        "canceled": _mo("canceled", status=OrderStatus.CANCELED),
        "rejected": _mo("rejected", status=OrderStatus.REJECTED),
        "denied": _mo("denied", status=OrderStatus.DENIED),
        "expired": _mo("expired", status=OrderStatus.EXPIRED),
        "liquidated": _mo("liquidated", status=OrderStatus.LIQUIDATED),
    }
    coids = {o.client_order_id for o in project_positions_orders(_FakeAccount({}), reg, "binance").orders}
    assert coids == {"live", "part"}


def test_market_order_price_is_none():
    reg = {"c1": _mo("c1", order_type="market", price=None)}
    o = project_positions_orders(_FakeAccount({}), reg, "binance").orders[0]
    assert o.order_type == "market" and o.price is None
