"""StrategyTester — the MT5-style facade. Phase 2a: .run(); .optimize()/.walk_forward() follow."""

from .backtester import Backtester
from .config import TesterConfig
from .report import TesterReport


class StrategyTester:
    """Front door over the tester layer. ``data`` is the bar list for the single-run path."""

    def __init__(self, strategy, data, config: TesterConfig | None = None):
        self.strategy = strategy
        self.data = data
        self.config = config or TesterConfig()

    def run(self) -> TesterReport:
        """Single historical backtest -> standardized report."""
        return Backtester(self.strategy, self.data, self.config).run()
