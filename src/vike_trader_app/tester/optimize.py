"""Optimization results: ranked per-combo TesterReports + the trial statistics for the verdict."""

from dataclasses import dataclass


@dataclass
class OptimizeTrial:
    """One parameter combination's outcome."""

    params: dict
    score: float
    report: object  # TesterReport


@dataclass
class OptimizeReport:
    """Ranked optimization output. ``best`` is ``ranked[0]``; trial stats feed the overfit verdict."""

    best: OptimizeTrial
    ranked: list
    trial_scores: list
    n_trials: int
    effective_n: float
    criterion: str = "sharpe"
