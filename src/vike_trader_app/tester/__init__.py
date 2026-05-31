"""tester — the Strategy Tester layer over core/ + analysis/ (never imports ui/).

A ``StrategyTester`` runs/optimizes/walk-forwards a strategy and returns a standardized
``TesterReport``. Phase 2a ships the single-run path; optimize/walk-forward follow.
"""

from .backtester import Backtester
from .config import TesterConfig
from .optimize import OptimizeReport, OptimizeTrial
from .report import TesterReport
from .strategy_tester import StrategyTester
from .walkforward import WalkForwardReport, WalkForwardWindow

__all__ = [
    "TesterConfig", "TesterReport", "Backtester", "StrategyTester",
    "OptimizeReport", "OptimizeTrial",
    "WalkForwardReport", "WalkForwardWindow",
]
