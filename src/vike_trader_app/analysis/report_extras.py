"""Extra backtest-report analytics for the Studio results — pure functions.

Per-trade returns, MFE/MAE excursions, a returns histogram, and CSV export. All operate on a
TesterReport (its ``trades`` + ``equity_curve``) plus the bars; no Qt, unit-tested. Used by the
results pane's Distribution view, the Trades-table MFE/MAE columns, and the Export-CSV action.
"""

from __future__ import annotations


def trade_returns(trades) -> list[float]:
    """Per-trade return fraction = ``pnl / (|size| * entry_price)`` (0 when the basis is 0)."""
    out = []
    for t in trades:
        basis = abs(t.size) * t.entry_price
        out.append(t.pnl / basis if basis else 0.0)
    return out


def mfe_mae(trades, bars) -> list[tuple[float, float]]:
    """``(MFE, MAE)`` per trade as return fractions over the bars the trade spanned.

    MFE = best unrealized return reached during the hold, MAE = worst. Longs use the bar
    highs/lows above/below entry; shorts invert. Falls back to the realized return when the
    trade's bars can't be located.
    """
    ts_index = {b.ts: i for i, b in enumerate(bars)}
    out: list[tuple[float, float]] = []
    for t in trades:
        i0 = ts_index.get(t.entry_ts)
        i1 = ts_index.get(t.exit_ts)
        basis = t.entry_price or 0.0
        if i0 is None or i1 is None or i1 < i0 or basis == 0:
            r = t.pnl / (abs(t.size) * basis) if (t.size and basis) else 0.0
            out.append((r, r))
            continue
        long = t.size >= 0
        best = worst = 0.0
        for b in bars[i0:i1 + 1]:
            up = (b.high - t.entry_price) / basis
            dn = (b.low - t.entry_price) / basis
            if long:
                best, worst = max(best, up), min(worst, dn)
            else:
                best, worst = max(best, -dn), min(worst, -up)
        out.append((best, worst))
    return out


def returns_histogram(returns, bins: int = 20):
    """``(edges, counts)`` histogram of ``returns`` over ``bins`` equal-width bins.

    ``edges`` has ``bins+1`` entries. Empty input -> ``([], [])``.
    """
    if not returns:
        return [], []
    lo, hi = min(returns), max(returns)
    if hi <= lo:
        hi = lo + 1e-9
    width = (hi - lo) / bins
    edges = [lo + i * width for i in range(bins + 1)]
    counts = [0] * bins
    for r in returns:
        idx = int((r - lo) / width)
        counts[min(idx, bins - 1)] += 1
    return edges, counts


def report_to_csv(report) -> str:
    """A two-section CSV: headline metrics, then the per-trade table."""
    lines = ["metric,value"]
    for k in ("n_trades", "total_return", "net_profit", "max_drawdown", "sharpe", "sortino",
              "profit_factor", "win_rate", "expected_payoff", "recovery_factor", "total_fees",
              "final_equity"):
        lines.append(f"{k},{getattr(report, k, '')}")
    lines.append("")
    lines.append("trade,side,entry_ts,exit_ts,entry,exit,size,pnl,fees")
    for i, t in enumerate(report.trades, 1):
        side = "long" if t.size >= 0 else "short"
        lines.append(f"{i},{side},{t.entry_ts},{t.exit_ts},{t.entry_price},"
                     f"{t.exit_price},{t.size},{t.pnl},{t.fees}")
    return "\n".join(lines) + "\n"
