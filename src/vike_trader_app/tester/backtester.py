"""Backtester — run one strategy over one bar set through the event engine -> TesterReport."""

from ..core.engine import SingleSymbolEngine
from .config import TesterConfig
from .report import TesterReport


class Backtester:
    """A single historical run. Wraps SingleSymbolEngine and standardizes the output."""

    def __init__(self, strategy, bars, config: TesterConfig | None = None):
        self.strategy = strategy
        self.bars = bars
        self.config = config or TesterConfig()

    def run(self) -> TesterReport:
        """Execute the backtest and return a standardized ``TesterReport``."""
        result = SingleSymbolEngine(self.bars, self.strategy, **self.config.engine_kwargs()).run()
        return TesterReport.from_result(result, periods_per_year=self.config.periods_per_year)
