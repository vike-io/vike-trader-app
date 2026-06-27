"""FillEvent-derived account read-model: positions + realized PnL from the fill stream.

A pure, Qt-free subscriber. ``apply_fill`` reproduces ``core.engine.BacktestEngine._apply_fill``'s
position branches (open / add-same-direction averaged cost / reduce / close-and-flip) so the realized
PnL on each closing portion — ``(price - avg_px) * (sign * closing) * multiplier`` — equals the
engine's ``Trade.pnl`` exactly (gross price PnL; commissions are carried on the FillEvent, not netted
here, mirroring the engine). Positions are keyed ``(venue, symbol, position_side)`` with
``position_side="BOTH"`` for one-way/spot — the tuple reserves the hedge-mode dimension for perps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vike_trader_app.core.fill import compute_fill

if TYPE_CHECKING:
    from vike_trader_app.exec.events import FillEvent, FundingEvent, PositionLiquidated


class Account:
    """Folds a stream of ``FillEvent``s into positions and realized PnL."""

    def __init__(self, multiplier: float = 1.0) -> None:
        self.multiplier = multiplier
        self.positions: dict[tuple[str, str, str], dict] = {}
        self.realized_pnl: float = 0.0
        self.trades: list[float] = []   # gross price PnL per closing portion, in order
        self.balance: float = 0.0
        self.marks: dict[tuple[str, str], float] = {}
        self.funding_paid: float = 0.0
        self.fees_paid: float = 0.0   # cumulative signed commission (>0 net cost, <0 net rebate)

    def apply_fill(self, fill: "FillEvent") -> None:
        key = (fill.venue, fill.symbol, fill.position_side)
        pos = self.positions.get(key)
        prior_size = pos["size"] if pos is not None else 0.0
        prior_avg = pos["avg_px"] if pos is not None else 0.0
        out = compute_fill(prior_size, prior_avg, fill.side, fill.last_qty, fill.last_px, self.multiplier)
        self.positions[key] = {"size": out.new_size, "avg_px": out.new_avg_px}  # rebind (not in-place mutate) — no external code aliases the inner position dict.
        if out.closing_qty > 0.0:                 # a reduce / close / flip realized PnL on the closed portion
            self.realized_pnl += out.realized_pnl
            self.trades.append(out.realized_pnl)
        # Net the trade commission into cash, like apply_liquidation nets its fee. Signed:
        # commission > 0 is a charge (lowers balance), < 0 is a maker rebate (raises it).
        self.balance -= fill.commission
        self.fees_paid += fill.commission

    def set_mark(self, venue: str, symbol: str, px: float) -> None:
        """Record the latest mark price for unrealized-PnL valuation (perp mark feed, slice 5+)."""
        self.marks[(venue, symbol)] = px

    def unrealized_pnl(self, venue: str, symbol: str, position_side: str = "BOTH") -> float:
        """Mark-to-market PnL on the open position. 0.0 if flat or no mark recorded yet.

        Same shape as compute_fill's realized line (core/fill.py:45) evaluated at the mark:
        (mark - avg_px) * size * multiplier (sign rides in the signed size).
        """
        pos = self.positions.get((venue, symbol, position_side))
        mark = self.marks.get((venue, symbol))
        if pos is None or mark is None:
            return 0.0
        return (mark - pos["avg_px"]) * pos["size"] * self.multiplier

    def apply_funding(self, ev: "FundingEvent") -> None:
        """Fold a periodic funding cashflow into the cash balance (signed: + received / - paid)."""
        self.balance += ev.amount
        self.funding_paid += ev.amount

    def apply_liquidation(self, ev: "PositionLiquidated") -> None:
        """Forced close: realize PnL at the liq price, close min(ev.qty, held), deduct the liq fee.

        Closes only the venue-reported quantity (``ev.qty``); the residual keeps its cost basis. When
        ``ev.qty`` is falsy (legacy whole-flatten callers / one-way full closes that omit qty) the WHOLE
        held size is closed — byte-equivalent to the pre-5g-1 behavior. ``ev.qty`` is clamped to the held
        size so an over-reported frame can never flip the position. Idempotent on a flat position (a
        same-object replay of a FULL close no-ops via the early return); replay safety for PARTIALs lives
        in LiveOmsHub._seen_liq_ids, mirroring FillEvent's _seen_trade_ids (the Account has no dedup layer).
        """
        key = (ev.venue, ev.symbol, ev.position_side)
        pos = self.positions.get(key)
        if pos is None or pos["size"] == 0.0:
            return   # true no-op: nothing to liquidate (idempotent on a replayed FULL close —
            # the fee was already deducted on the real close; deducting it here would double-charge)
        close_side = -1 if pos["size"] > 0.0 else 1      # close on the opposite side
        close_qty = min(abs(ev.qty), abs(pos["size"])) if ev.qty else abs(pos["size"])
        out = compute_fill(pos["size"], pos["avg_px"], close_side, close_qty,
                           ev.liq_price, self.multiplier)
        self.positions[key] = {"size": out.new_size, "avg_px": out.new_avg_px}
        if out.closing_qty > 0.0:
            self.realized_pnl += out.realized_pnl
            self.trades.append(out.realized_pnl)
        self.balance -= ev.fee
