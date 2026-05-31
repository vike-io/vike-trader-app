"""Walk-forward results: per-window OOS + the stitched report carrying the overfit verdict."""

from dataclasses import dataclass


@dataclass
class WalkForwardWindow:
    """One walk-forward step: params optimized on train, measured OOS on test."""

    train_range: tuple
    test_range: tuple
    best_params: dict
    oos_report: object  # TesterReport for the test slice


@dataclass
class WalkForwardReport:
    """Per-window results + the stitched OOS TesterReport (``.verdict`` attached) + consistency."""

    windows: list
    oos_report: object
    wf_consistency: float
    n_windows: int


def _pbo_from_curves(curves, n_splits: int = 4) -> float:
    """PBO via CSCV over trial equity-curve returns; 0.0 when not buildable (<2 trials / too short)."""
    from ..analysis.overfit import pbo_cscv

    rets = []
    for c in curves:
        rets.append([c[i] / c[i - 1] - 1.0 for i in range(1, len(c)) if c[i - 1] != 0])
    if len(rets) < 2:
        return 0.0
    min_len = min((len(r) for r in rets), default=0)
    if min_len < n_splits:
        return 0.0
    matrix = [[rets[j][t] for j in range(len(rets))] for t in range(min_len)]
    try:
        return pbo_cscv(matrix, n_splits)
    except Exception:
        return 0.0
