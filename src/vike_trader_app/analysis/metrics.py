"""Performance metrics computed from an equity curve and the trade list.

Pure functions over plain lists — no engine or GUI dependency.
"""

import math


def total_return(equity_curve: list[float]) -> float:
    """Fractional return from first to last equity point (0.01 == 1%)."""
    if len(equity_curve) < 2 or equity_curve[0] == 0:
        return 0.0
    return equity_curve[-1] / equity_curve[0] - 1.0


def win_rate(trades) -> float:
    """Fraction of trades with positive PnL (0..1)."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t.pnl > 0)
    return wins / len(trades)


def max_drawdown(equity_curve: list[float]) -> float:
    """Largest peak-to-trough drop as a positive fraction of the peak (0.2 == 20%)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    worst = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def profit_factor(trades) -> float:
    """Gross profit / gross loss. ``inf`` when there are no losing trades."""
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = -sum(t.pnl for t in trades if t.pnl < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def sortino(equity_curve: list[float], periods_per_year: float = 365 * 24 * 60) -> float:
    """Annualized Sortino ratio of per-bar returns (target = 0, risk-free = 0).

    Like ``sharpe`` but the denominator is the downside deviation — the sample std of the
    *negative* return deviations only. 0.0 if there is no downside or fewer than 2 returns.
    """
    if len(equity_curve) < 2:
        return 0.0
    returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] != 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downside_var = sum(min(0.0, r) ** 2 for r in returns) / (len(returns) - 1)
    downside_dev = math.sqrt(downside_var)
    if downside_dev == 0:
        return 0.0
    return (mean / downside_dev) * math.sqrt(periods_per_year)


def calmar(equity_curve: list[float], periods_per_year: float = 365 * 24 * 60) -> float:
    """Annualized return (CAGR) divided by max drawdown.

    ``inf`` when there is positive growth and zero drawdown; 0.0 for a flat/short curve or
    non-positive growth with zero drawdown.
    """
    if len(equity_curve) < 2 or equity_curve[0] <= 0:
        return 0.0
    n_periods = len(equity_curve) - 1
    growth = equity_curve[-1] / equity_curve[0]
    cagr = growth ** (periods_per_year / n_periods) - 1.0
    mdd = max_drawdown(equity_curve)
    if mdd == 0:
        return float("inf") if cagr > 0 else 0.0
    return cagr / mdd


def omega(equity_curve: list[float], threshold: float = 0.0) -> float:
    """Omega ratio of per-bar returns: total gains above ``threshold`` / total losses below it.

    ``inf`` when there are gains and no losses; 0.0 when there are no gains.
    """
    if len(equity_curve) < 2:
        return 0.0
    returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] != 0
    ]
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = sum(threshold - r for r in returns if r < threshold)
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def sharpe(equity_curve: list[float], periods_per_year: float = 365 * 24 * 60) -> float:
    """Annualized Sharpe of per-bar returns (risk-free = 0). 0.0 if variance is 0.

    Default ``periods_per_year`` assumes 1-minute bars.
    """
    if len(equity_curve) < 2:
        return 0.0
    returns = [
        equity_curve[i] / equity_curve[i - 1] - 1.0
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] != 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def net_profit(trades) -> float:
    """Sum of gross trade PnL (matches the win_rate/profit_factor convention: uses t.pnl)."""
    return sum(t.pnl for t in trades)


def gross_profit(trades) -> float:
    """Sum of PnL over winning trades."""
    return sum(t.pnl for t in trades if t.pnl > 0)


def gross_loss(trades) -> float:
    """Absolute value of summed PnL over losing trades (>= 0)."""
    return -sum(t.pnl for t in trades if t.pnl < 0)


def total_fees(trades) -> float:
    """Sum of round-trip fees across trades."""
    return sum(t.fees for t in trades)


def expected_payoff(trades) -> float:
    """Average gross PnL per trade (0.0 if there are no trades)."""
    return net_profit(trades) / len(trades) if trades else 0.0


def recovery_factor(equity_curve: list[float]) -> float:
    """Total return divided by max drawdown (both fractions). ``inf`` when there's no drawdown."""
    dd = max_drawdown(equity_curve)
    if dd == 0:
        return float("inf") if total_return(equity_curve) > 0 else 0.0
    return total_return(equity_curve) / dd


def _max_run(trades, win: bool) -> int:
    best = run = 0
    for t in trades:
        hit = t.pnl > 0 if win else t.pnl < 0
        run = run + 1 if hit else 0
        best = max(best, run)
    return best


def consecutive_wins(trades) -> int:
    """Longest run of consecutive winning trades."""
    return _max_run(trades, win=True)


def consecutive_losses(trades) -> int:
    """Longest run of consecutive losing trades."""
    return _max_run(trades, win=False)


def largest_win(trades) -> float:
    """Largest single-trade PnL (0.0 if no winners)."""
    wins = [t.pnl for t in trades if t.pnl > 0]
    return max(wins) if wins else 0.0


def largest_loss(trades) -> float:
    """Most negative single-trade PnL (0.0 if no losers)."""
    losses = [t.pnl for t in trades if t.pnl < 0]
    return min(losses) if losses else 0.0


def avg_win(trades) -> float:
    """Mean PnL of winning trades (0.0 if none)."""
    wins = [t.pnl for t in trades if t.pnl > 0]
    return sum(wins) / len(wins) if wins else 0.0


def avg_loss(trades) -> float:
    """Mean PnL of losing trades (0.0 if none)."""
    losses = [t.pnl for t in trades if t.pnl < 0]
    return sum(losses) / len(losses) if losses else 0.0
