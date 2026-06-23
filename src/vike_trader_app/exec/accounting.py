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

    def apply_fill(self, fill: "FillEvent") -> None:
        key = (fill.venue, fill.symbol, "BOTH")
        pos = self.positions.get(key)
        prior_size = pos["size"] if pos is not None else 0.0
        prior_avg = pos["avg_px"] if pos is not None else 0.0
        out = compute_fill(prior_size, prior_avg, fill.side, fill.last_qty, fill.last_px, self.multiplier)
        self.positions[key] = {"size": out.new_size, "avg_px": out.new_avg_px}
        if out.closing_qty > 0.0:                 # a reduce / close / flip realized PnL on the closed portion
            self.realized_pnl += out.realized_pnl
            self.trades.append(out.realized_pnl)
