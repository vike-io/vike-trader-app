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
    from vike_trader_app.exec.events import FillEvent


class Account:
    """Folds a stream of ``FillEvent``s into positions and realized PnL."""

    def __init__(self, multiplier: float = 1.0) -> None:
        self.multiplier = multiplier
        self.positions: dict[tuple[str, str, str], dict] = {}
        self.realized_pnl: float = 0.0
        self.trades: list[float] = []   # gross price PnL per closing portion, in order
        self.balance: float = 0.0
        self.marks: dict[tuple[str, str], float] = {}

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
