"""Walk-forward optimization: re-optimize per train window, score out-of-sample, stitch.

Anti-overfit by construction — reported performance is the concatenation of
out-of-sample test windows, never the in-sample optimum. Builds on
``validation.walk_forward_splits`` and ``optimizer.grid_search``.
"""

from dataclasses import dataclass, field

from ..core.engine import BacktestEngine
from .metrics import sharpe
from .optimizer import grid_search
from .validation import walk_forward_splits


@dataclass
class WFWindow:
    """One walk-forward step: params optimized on train, measured on test (OOS)."""

    train_range: tuple
    test_range: tuple
    best_params: dict
    is_score: float
    oos_return: float
    oos_result: object = field(default=None, repr=False)


@dataclass
class WalkForwardReport:
    """Aggregate of all walk-forward windows + the stitched OOS curve."""

    windows: list
    oos_equity_curve: list
    oos_return: float
    oos_sharpe: float
    mean_is_score: float


def walk_forward_optimize(
    bars, make, param_grid, n_splits: int = 4, score_fn=None, fee_rate: float = 0.0, cash: float = 10_000.0
):
    """Optimize on each train window, evaluate the winner OOS, and stitch the results."""
    splits = walk_forward_splits(len(bars), n_splits)
    windows: list[WFWindow] = []
    stitched: list[float] = []
    equity = cash
    for tr_s, tr_e, te_s, te_e in splits:
        train, test = bars[tr_s:tr_e], bars[te_s:te_e]
        best = grid_search(train, make, param_grid, score_fn=score_fn, fee_rate=fee_rate)[0]
        oos = BacktestEngine(test, make(**best.params), fee_rate=fee_rate, cash=cash).run()
        start = equity
        for v in oos.equity_curve:  # scale window curve to continue from running equity
            stitched.append(start * (v / cash))
        equity = start * (oos.final_equity / cash)
        windows.append(
            WFWindow(
                train_range=(tr_s, tr_e),
                test_range=(te_s, te_e),
                best_params=best.params,
                is_score=best.score,
                oos_return=(oos.final_equity / cash) - 1.0,
                oos_result=oos,
            )
        )
    mean_is = sum(w.is_score for w in windows) / len(windows) if windows else 0.0
    return WalkForwardReport(
        windows=windows,
        oos_equity_curve=stitched,
        oos_return=(equity / cash) - 1.0,
        oos_sharpe=sharpe(stitched),
        mean_is_score=mean_is,
    )
