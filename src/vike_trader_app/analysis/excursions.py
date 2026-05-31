"""Trade-level excursion analytics computed post-hoc from the trade list and OHLC bars.

Pure functions — no engine dependency. MAE/MFE (Maximum Adverse/Favorable Excursion) are
derived by scanning bars between each trade's entry and exit timestamps.
"""


def _direction(trade) -> int:
    """Infer +1 (long) / -1 (short) from an unsigned-size Trade.

    A profit on a price rise is a long; a profit on a price fall is a short. The degenerate
    ``pnl == 0`` / ``exit == entry`` case defaults to long.
    """
    return 1 if (trade.pnl >= 0) == (trade.exit_price >= trade.entry_price) else -1


def mae_mfe(trade, bars, direction: int | None = None) -> tuple[float, float]:
    """Return ``(mae, mfe)`` as positive fractions of entry price over the trade's bar window.

    MAE = worst adverse excursion, MFE = best favorable excursion, between ``entry_ts`` and
    ``exit_ts`` (inclusive). ``direction`` overrides the inferred long/short side.
    """
    entry = trade.entry_price
    if entry == 0:
        return (0.0, 0.0)
    d = _direction(trade) if direction is None else direction
    window = [b for b in bars if trade.entry_ts <= b.ts <= trade.exit_ts]
    if not window:
        return (0.0, 0.0)
    if d > 0:  # long: adverse = low below entry, favorable = high above entry
        mae = max(0.0, max((entry - b.low) / entry for b in window))
        mfe = max(0.0, max((b.high - entry) / entry for b in window))
    else:      # short: adverse = high above entry, favorable = low below entry
        mae = max(0.0, max((b.high - entry) / entry for b in window))
        mfe = max(0.0, max((entry - b.low) / entry for b in window))
    return (mae, mfe)


def edge_ratio(trades, bars) -> float:
    """Mean MFE / mean MAE across ``trades`` — entry-quality (>1 favors profitable excursions).

    0.0 with no trades; ``inf`` when mean MAE is 0 and mean MFE is positive.
    """
    if not trades:
        return 0.0
    pairs = [mae_mfe(t, bars) for t in trades]
    mean_mae = sum(m for m, _ in pairs) / len(pairs)
    mean_mfe = sum(f for _, f in pairs) / len(pairs)
    if mean_mae == 0:
        return float("inf") if mean_mfe > 0 else 0.0
    return mean_mfe / mean_mae


def expanding_trade_metrics(trades) -> list[dict]:
    """Running metrics after each trade: ``n``, ``win_rate``, ``profit_factor``, ``avg_pnl``.

    ``profit_factor`` is ``inf`` while there are no losing trades and gross profit is positive.
    """
    out = []
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    total_pnl = 0.0
    for i, t in enumerate(trades, start=1):
        if t.pnl > 0:
            wins += 1
            gross_profit += t.pnl
        elif t.pnl < 0:
            gross_loss += -t.pnl
        total_pnl += t.pnl
        if gross_loss == 0:
            pf = float("inf") if gross_profit > 0 else 0.0
        else:
            pf = gross_profit / gross_loss
        out.append({
            "n": i,
            "win_rate": wins / i,
            "profit_factor": pf,
            "avg_pnl": total_pnl / i,
        })
    return out
