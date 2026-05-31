"""TesterReport — one standardized result (MT5/QuantConnect-grade metric coverage).

Built from an engine ``Result`` via ``from_result``; reuses ``analysis.metrics``. Carries the
trades + equity curve so the GUI/tearsheet can render without recomputation. (The anti-overfit
verdict is attached later, in the optimize/walk-forward phases.)
"""

from dataclasses import dataclass, field

from ..analysis import metrics as m


@dataclass
class TesterReport:
    """Standardized backtest metrics + the underlying trades/equity curve."""

    n_trades: int
    final_equity: float
    total_return: float
    net_profit: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    expected_payoff: float
    recovery_factor: float
    max_drawdown: float
    sharpe: float
    sortino: float
    calmar: float
    omega: float
    win_rate: float
    pct_profitable: float
    largest_win: float
    largest_loss: float
    avg_win: float
    avg_loss: float
    consecutive_wins: int
    consecutive_losses: int
    total_fees: float
    trades: list = field(default_factory=list, repr=False)
    equity_curve: list = field(default_factory=list, repr=False)

    @classmethod
    def from_result(cls, result, periods_per_year: float = 365 * 24 * 60) -> "TesterReport":
        """Compute the standardized metric set from an engine ``Result``."""
        eq = result.equity_curve
        tr = result.trades
        return cls(
            n_trades=len(tr),
            final_equity=result.final_equity,
            total_return=m.total_return(eq),
            net_profit=m.net_profit(tr),
            gross_profit=m.gross_profit(tr),
            gross_loss=m.gross_loss(tr),
            profit_factor=m.profit_factor(tr),
            expected_payoff=m.expected_payoff(tr),
            recovery_factor=m.recovery_factor(eq),
            max_drawdown=m.max_drawdown(eq),
            sharpe=m.sharpe(eq, periods_per_year),
            sortino=m.sortino(eq, periods_per_year),
            calmar=m.calmar(eq, periods_per_year),
            omega=m.omega(eq),
            win_rate=m.win_rate(tr),
            pct_profitable=m.win_rate(tr),
            largest_win=m.largest_win(tr),
            largest_loss=m.largest_loss(tr),
            avg_win=m.avg_win(tr),
            avg_loss=m.avg_loss(tr),
            consecutive_wins=m.consecutive_wins(tr),
            consecutive_losses=m.consecutive_losses(tr),
            total_fees=m.total_fees(tr),
            trades=tr,
            equity_curve=eq,
        )

    def as_dict(self) -> dict:
        """Headline metrics as a plain dict (for UI tables / JSON), excluding the bulky series."""
        from dataclasses import asdict
        d = asdict(self)
        d.pop("trades", None)
        d.pop("equity_curve", None)
        return d
