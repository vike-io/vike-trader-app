"""End-to-end anti-overfit report: optimize, then judge the winner for overfitting.

Ties the optimizer + deflated Sharpe + PBO + verdict into one object the UI/CLI can
show. The headline is a retail-readable "overfit risk" verdict.
"""

from dataclasses import dataclass

from .metrics import returns, sharpe
from .optimizer import grid_search
from .overfit import Verdict, deflated_sharpe_ratio, overfit_verdict, pbo_cscv


@dataclass
class OverfitReport:
    """Result of validating an optimization for overfitting."""

    best_params: dict
    best_sharpe: float  # annualized, for display
    deflated_sharpe: float
    pbo: float
    n_trials: int
    verdict: Verdict


def build_overfit_report(bars, make, param_grid, n_splits: int = 4, fee_rate: float = 0.0):
    """Optimize ``make`` over ``param_grid`` and assess the best config for overfitting."""
    # Rank by per-observation Sharpe so the headline matches the DSR input.
    results = grid_search(
        bars, make, param_grid, score_fn=lambda r: sharpe(r.equity_curve, 1), fee_rate=fee_rate
    )
    best = results[0]
    trial_sharpes = [r.score for r in results]
    n_obs = max(len(best.result.equity_curve) - 1, 2)
    dsr = deflated_sharpe_ratio(best.score, trial_sharpes, n_obs)

    # PBO: per-observation returns of every trial form the T x N matrix.
    trial_returns = [returns(r.result.equity_curve) for r in results]
    min_len = min((len(r) for r in trial_returns), default=0)
    pbo = 0.0
    if len(results) >= 2 and min_len >= n_splits:
        matrix = [[trial_returns[j][t] for j in range(len(results))] for t in range(min_len)]
        pbo = pbo_cscv(matrix, n_splits)

    return OverfitReport(
        best_params=best.params,
        best_sharpe=sharpe(best.result.equity_curve),  # annualized for display
        deflated_sharpe=dsr,
        pbo=pbo,
        n_trials=len(results),
        verdict=overfit_verdict(pbo, dsr),
    )
