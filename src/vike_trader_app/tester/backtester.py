"""Backtester — run one strategy over one bar set through the event engine -> TesterReport.

mirror=False (default; optimizer-trial/sweep): engine via config.engine_kwargs(), NO on_fill hook,
NO Account — byte-identical pre-S5 hot loop. mirror=True (single REPORT run, set by
StrategyTester.run): attach a passive SimulatedExchange + EventBus + decomposed Account for the
report's PnL/fees/funding split without perturbing the engine's canonical scalar equity curve.
"""

from __future__ import annotations

from ..core.single_symbol_engine import SingleSymbolEngine
from ..exec.accounting import Account
from ..exec.bus import EventBus
from ..exec.sim_exchange import SimulatedExchange
from .config import TesterConfig
from .report import TesterReport


class Backtester:
    """A single historical run. Wraps SingleSymbolEngine and standardizes the output."""

    def __init__(self, strategy, bars, config: TesterConfig | None = None, *, mirror: bool = False):
        self.strategy = strategy
        self.bars = bars
        self.config = config or TesterConfig()
        self.mirror = mirror
        self.sim_account: Account | None = None  # set by run() when mirror=True; None on the sweep path

    def run(self) -> TesterReport:
        """Execute the backtest and return a standardized ``TesterReport``.

        mirror=False (default): bare engine via config.engine_kwargs() — the byte-identical
        optimizer/sweep path; no Account is constructed and no hooks are attached.

        mirror=True: attaches a passive SimulatedExchange + Account on the bus so fills fold into
        the decomposed ledger. The engine's scalar equity curve remains the canonical source of
        truth for every number in TesterReport; the Account is a sparse side-channel read-model.
        """
        if not self.mirror:
            result = SingleSymbolEngine(self.bars, self.strategy, **self.config.engine_kwargs()).run()
            return TesterReport.from_result(result, periods_per_year=self.config.periods_per_year)

        bus = EventBus()
        acc = Account(multiplier=self.config.multiplier)
        eng = SingleSymbolEngine(self.bars, self.strategy, **self.config.engine_kwargs())
        SimulatedExchange(eng, bus, venue="sim", symbol="X", sim_account=acc)
        result = eng.run()
        self.sim_account = acc
        return TesterReport.from_result(result, periods_per_year=self.config.periods_per_year)
