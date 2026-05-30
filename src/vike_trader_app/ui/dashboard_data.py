"""Qt-free data prep for the optimizer dashboard — the 4 result charts + the heatmap.

Keeps all logic testable and out of the Qt widget (which only plots these arrays),
matching the ``chartdata.py`` convention.
"""

from ..analysis.heatmap import heatmap_grid


def drawdown_curve(equity) -> list[float]:
    """Per-bar drawdown from the running peak (<= 0)."""
    out: list[float] = []
    peak = equity[0] if equity else 0.0
    for v in equity:
        peak = max(peak, v)
        out.append((v - peak) / peak if peak else 0.0)
    return out


def per_bar_pnl(equity) -> list[float]:
    """Bar-over-bar equity change (the daily-P&L bars)."""
    return [equity[i] - equity[i - 1] for i in range(1, len(equity))]


def return_histogram(equity, bins: int = 20):
    """Distribution of per-bar returns -> ``(bin_centers, counts)``."""
    rets = [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1]]
    if not rets:
        return [], []
    lo, hi = min(rets), max(rets)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for r in rets:
        b = min(bins - 1, int((r - lo) / span * bins))
        counts[b] += 1
    centers = [lo + (i + 0.5) * span / bins for i in range(bins)]
    return centers, counts


def sharpe_heatmap(bars, make, x_name, x_vals, y_name, y_vals, score_fn=None, fee_rate: float = 0.0):
    """2-parameter score surface -> ``(x_vals, y_vals, scores[y][x])`` for an ImageItem."""
    hm = heatmap_grid(
        bars, make, param_x=x_name, values_x=x_vals, param_y=y_name, values_y=y_vals,
        score_fn=score_fn, fee_rate=fee_rate,
    )
    return hm.values_x, hm.values_y, hm.scores
