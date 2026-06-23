"""FillEvent-derived account read-model: positions + realized PnL from the fill stream.

A pure, Qt-free subscriber. ``apply_fill`` reproduces ``core.engine.BacktestEngine._apply_fill``'s
position branches (open / add-same-direction averaged cost / reduce / close-and-flip) so the realized
PnL on each closing portion — ``(price - avg_px) * (sign * closing) * multiplier`` — equals the
engine's ``Trade.pnl`` exactly (gross price PnL; commissions are carried on the FillEvent, not netted
here, mirroring the engine). Positions are keyed ``(venue, symbol, position_side)`` with
``position_side="BOTH"`` for one-way/spot — the tuple reserves the hedge-mode dimension for perps.
"""

from __future__ import annotations

_EPS = 1e-12


class Account:
    """Folds a stream of ``FillEvent``s into positions and realized PnL."""

    def __init__(self, multiplier: float = 1.0) -> None:
        self.multiplier = multiplier
        self.positions: dict[tuple[str, str, str], dict] = {}
        self.realized_pnl: float = 0.0
        self.trades: list[float] = []   # gross price PnL per closing portion, in order

    def apply_fill(self, fill) -> None:
        key = (fill.venue, fill.symbol, "BOTH")
        pos = self.positions.get(key)
        delta = fill.side * fill.last_qty
        price = fill.last_px
        if pos is None or pos["size"] == 0.0:                     # open
            self.positions[key] = {"size": delta, "avg_px": price}
            return
        if (pos["size"] > 0.0) == (delta > 0.0):                 # add in the same direction
            new_size = pos["size"] + delta
            pos["avg_px"] = (pos["avg_px"] * abs(pos["size"]) + price * abs(delta)) / abs(new_size)
            pos["size"] = new_size
            return
        # opposite direction: reduce / fully close / close-and-flip
        sign = 1.0 if pos["size"] > 0.0 else -1.0
        closing = min(abs(delta), abs(pos["size"]))
        pnl = (price - pos["avg_px"]) * (sign * closing) * self.multiplier
        self.realized_pnl += pnl
        self.trades.append(pnl)
        remaining = abs(pos["size"]) - closing
        if remaining > _EPS:                                     # partial reduce: remainder at cost
            pos["size"] = sign * remaining
            return
        leftover = abs(delta) - closing                          # crossed zero -> open opposite
        if leftover > _EPS:
            pos["size"] = (1.0 if delta > 0.0 else -1.0) * leftover
            pos["avg_px"] = price
        else:                                                    # flat
            pos["size"] = 0.0
            pos["avg_px"] = 0.0
