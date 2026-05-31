"""TesterConfig — the run configuration; maps 1:1 onto BacktestEngine's keyword args."""

from dataclasses import dataclass


@dataclass
class TesterConfig:
    """Costs + capital + contract settings for a tester run. ``engine_kwargs`` feeds BacktestEngine."""

    cash: float = 10_000.0
    fee_rate: float = 0.0
    maker_fee: float | None = None
    taker_fee: float = 0.0
    slippage: float = 0.0
    multiplier: float = 1.0
    leverage: float | None = None
    maint_margin: float = 0.0
    timeframes: list[str] | None = None
    cashflows: list[float] | None = None
    periods_per_year: float = 365 * 24 * 60  # 1-minute bars; for annualized Sharpe/Sortino/Calmar

    def engine_kwargs(self) -> dict:
        """The exact keyword arguments for ``BacktestEngine(bars, strategy, **engine_kwargs())``."""
        return {
            "fee_rate": self.fee_rate,
            "cash": self.cash,
            "timeframes": self.timeframes,
            "slippage": self.slippage,
            "maker_fee": self.maker_fee,
            "taker_fee": self.taker_fee,
            "multiplier": self.multiplier,
            "leverage": self.leverage,
            "maint_margin": self.maint_margin,
            "cashflows": self.cashflows,
        }
