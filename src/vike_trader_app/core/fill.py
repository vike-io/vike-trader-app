"""The ONE cost-basis primitive: how a fill transitions a position.

`compute_fill` is pure — it computes the open / add / reduce / close / flip transition (new size, new
average price, the closed quantity, gross realized PnL, the fee-apportionment `portion`, and the
flip `leftover`) and returns it as a `FillOutcome`. Callers (`core.engine`, `core.portfolio`,
`exec.accounting`) keep their own cash / fee / trade-record / per-symbol bookkeeping and only delegate
this shared math, so single-symbol backtest, portfolio backtest, and the live read-model can never
drift. The Numba `fastsim` kernel keeps its own inline mirror (Numba can't call Python) — parity-locked
as today. The branch math here is byte-identical to the prior `engine._apply_fill`/`portfolio._apply_fill`.
"""

from __future__ import annotations

from dataclasses import dataclass

_EPS = 1e-12


@dataclass(frozen=True)
class FillOutcome:
    kind: str           # "open" | "add" | "reduce" | "flip" | "close"
    new_size: float     # position size after the fill
    new_avg_px: float   # average price after the fill
    closing_qty: float  # units of the prior position retired (0 for open/add)
    entry_avg_px: float # avg price BEFORE the fill (for the closed portion's trade record; 0 for open/add)
    realized_pnl: float # gross price PnL on closing_qty (signed; 0 for open/add)
    portion: float      # closing_qty / abs(prior_size) — for entry-fee apportionment (0 for open/add)
    leftover: float     # qty opened on the opposite side after a flip (0 otherwise)


def compute_fill(prior_size: float, prior_avg_px: float, side: int, qty: float,
                 price: float, multiplier: float = 1.0) -> FillOutcome:
    """Compute the position transition for a fill of ``qty`` at ``price`` on ``side`` (+1/-1)."""
    delta = side * qty
    if prior_size == 0.0:                                   # open from flat; exact-zero, matching the engines' `== 0`; a closed position is hard-set to 0.0, never an epsilon residue — do NOT change to abs()<_EPS or it diverges from engine/portfolio.
        return FillOutcome("open", delta, price, 0.0, 0.0, 0.0, 0.0, 0.0)
    if (prior_size > 0.0) == (delta > 0.0):                 # add in the same direction
        new_size = prior_size + delta
        new_avg = (prior_avg_px * abs(prior_size) + price * abs(delta)) / abs(new_size)
        return FillOutcome("add", new_size, new_avg, 0.0, 0.0, 0.0, 0.0, 0.0)
    # opposite direction: reduce / fully close / close-and-flip
    sign = 1.0 if prior_size > 0.0 else -1.0
    closing = min(abs(delta), abs(prior_size))
    portion = closing / abs(prior_size)
    realized = (price - prior_avg_px) * (sign * closing) * multiplier   # signed -> shorts ok
    remaining = abs(prior_size) - closing
    if remaining > _EPS:                                    # partial reduce: remainder keeps cost basis
        return FillOutcome("reduce", sign * remaining, prior_avg_px, closing, prior_avg_px,
                           realized, portion, 0.0)
    leftover = abs(delta) - closing
    if leftover > _EPS:                                     # crossed zero -> open opposite at fill price
        new_size = (1.0 if delta > 0.0 else -1.0) * leftover
        return FillOutcome("flip", new_size, price, closing, prior_avg_px, realized, portion, leftover)
    return FillOutcome("close", 0.0, 0.0, closing, prior_avg_px, realized, portion, 0.0)  # flat
