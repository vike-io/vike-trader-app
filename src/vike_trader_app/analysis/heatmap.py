"""Two-parameter optimization heatmap data.

Runs the backtest across the cartesian product of two parameter ranges and returns a
2D score matrix (rows = y values, cols = x values) ready for a GUI heatmap render
(pyqtgraph) — the iconic fast x slow Sharpe surface. Pure/Qt-free; rendering is a
separate UI concern.
"""

from dataclasses import dataclass

from ..core.engine import BacktestEngine
from .metrics import sharpe


@dataclass
class Heatmap:
    """A 2D grid of scores plus the axis values that produced them."""

    param_x: str
    param_y: str
    values_x: list
    values_y: list
    scores: list  # scores[y_index][x_index]


def heatmap_grid(bars, make, *, param_x, values_x, param_y, values_y, score_fn=None, fee_rate: float = 0.0):
    """Build a :class:`Heatmap` by backtesting every (x, y) parameter combination."""
    score_fn = score_fn or (lambda r: sharpe(r.equity_curve))
    scores = []
    for y in values_y:
        row = []
        for x in values_x:
            strat = make(**{param_x: x, param_y: y})
            res = BacktestEngine(bars, strat, fee_rate=fee_rate).run()
            row.append(score_fn(res))
        scores.append(row)
    return Heatmap(param_x=param_x, param_y=param_y, values_x=list(values_x), values_y=list(values_y), scores=scores)
