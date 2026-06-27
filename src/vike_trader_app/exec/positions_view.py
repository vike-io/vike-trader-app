"""Qt-free projector: an armed hub's Account + ManagedOrder registry -> panel rows.

Pure + unit-testable (mirrors exec/order_ticket.py). The OPEN-ORDERS view FILTERS terminal statuses
because the LiveOmsHub registry is never pruned (a CANCELED/FILLED order lingers, live_oms.py:96-97) —
filtering here is what makes a canceled row vanish on the next refresh. Positions iterate EVERY
position_side leg (BOTH/LONG/SHORT) for this venue, skipping flat legs; mark is None when no mark feed
(spot) so uPnL renders as 0.0 / '—', not an error (accounting.py:56).
"""
from __future__ import annotations

from dataclasses import dataclass

from vike_trader_app.exec.order import OrderStatus

# No is_terminal() on OrderStatus — derive from the enum comments (order.py:37-44).
_TERMINAL = frozenset({
    OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED,
    OrderStatus.DENIED, OrderStatus.EXPIRED, OrderStatus.LIQUIDATED,
})


@dataclass(frozen=True)
class PositionRow:
    venue: str
    symbol: str
    position_side: str
    size: float
    avg_px: float
    mark: float | None
    unrealized_pnl: float


@dataclass(frozen=True)
class OrderRow:
    client_order_id: str
    symbol: str
    side: int
    qty: float
    order_type: str
    status: str
    filled_qty: float
    avg_fill_px: float
    price: float | None


@dataclass(frozen=True)
class PanelRows:
    positions: tuple[PositionRow, ...] = ()
    orders: tuple[OrderRow, ...] = ()
    balance: float = 0.0
    realized_pnl: float = 0.0


def project_positions_orders(account, registry, venue) -> PanelRows:
    """Project the armed hub's read-model into panel rows. Pure; no Qt, no mutation."""
    positions: list[PositionRow] = []
    for (v, symbol, side), pos in account.positions.items():
        if v != venue:
            continue                       # account-wide model: only THIS hub's venue
        size = pos["size"]
        if size == 0.0:
            continue                       # flat leg — not an open position
        mark = account.marks.get((v, symbol))
        positions.append(PositionRow(
            venue=v, symbol=symbol, position_side=side, size=size, avg_px=pos["avg_px"],
            mark=mark, unrealized_pnl=account.unrealized_pnl(v, symbol, side)))

    orders: list[OrderRow] = []
    for mo in registry.values():
        if mo.status in _TERMINAL:
            continue                       # registry keeps terminal orders; open-orders view excludes them
        req = mo.request
        orders.append(OrderRow(
            client_order_id=mo.client_order_id, symbol=req.symbol, side=req.side, qty=req.qty,
            order_type=req.order_type, status=mo.status.value, filled_qty=mo.filled_qty,
            avg_fill_px=mo.avg_fill_px, price=req.price))

    return PanelRows(positions=tuple(positions), orders=tuple(orders),
                     balance=account.balance, realized_pnl=account.realized_pnl)
