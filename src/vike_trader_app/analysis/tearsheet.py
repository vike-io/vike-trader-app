"""Standalone HTML tearsheet: inline-SVG equity + drawdown charts, stats, trade log.

Pure string building — no matplotlib/plotly/JS. The output file is fully
self-contained and openable in any browser. Reuses analysis.metrics.
"""

from datetime import datetime, timezone
from pathlib import Path

from . import metrics


def monthly_returns(timestamps, equity):
    """Returns per calendar month as ``[(YYYY-MM, fractional_return), ...]``.

    Buckets the equity curve by month (UTC) and chains end-of-month equity from the
    initial value. Requires per-bar timestamps (epoch ms) aligned to ``equity``.
    """
    if not timestamps or len(timestamps) != len(equity):
        return []
    month_end: dict[str, float] = {}
    order: list[str] = []
    for ts, eq in zip(timestamps, equity, strict=True):
        label = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m")
        if label not in month_end:
            order.append(label)
        month_end[label] = eq  # last seen wins => month-end
    out = []
    prev = equity[0]
    for label in order:
        end = month_end[label]
        out.append((label, end / prev - 1.0 if prev else 0.0))
        prev = end
    return out


def _polyline_svg(values, width=720, height=160, pad=8) -> str:
    """Render a series as an SVG <polyline> scaled to the viewport."""
    if not values:
        return f'<svg width="{width}" height="{height}"></svg>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    step = (width - 2 * pad) / max(n - 1, 1)
    pts = []
    for i, v in enumerate(values):
        x = pad + i * step
        y = height - pad - (v - lo) / span * (height - 2 * pad)
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg width="{width}" height="{height}" style="background:#0d1117">'
        f'<polyline fill="none" stroke="#ff6a00" stroke-width="1.5" points="{" ".join(pts)}"/></svg>'
    )


def _drawdown_series(equity):
    out, peak = [], equity[0] if equity else 0.0
    for v in equity:
        peak = max(peak, v)
        out.append((v - peak) / peak if peak else 0.0)
    return out


def _bar_returns(equity):
    return [equity[i] / equity[i - 1] - 1.0 for i in range(1, len(equity)) if equity[i - 1]]


def _rolling_sharpe(equity, window: int = 30, periods_per_year: float = 365 * 24 * 60):
    """Rolling annualized Sharpe of per-bar returns over ``window`` (defined tail only)."""
    rets = _bar_returns(equity)
    out = []
    for i in range(window - 1, len(rets)):
        w = rets[i - window + 1 : i + 1]
        mean = sum(w) / len(w)
        var = sum((r - mean) ** 2 for r in w) / (len(w) - 1) if len(w) > 1 else 0.0
        sd = var**0.5
        out.append((mean / sd) * (periods_per_year**0.5) if sd else 0.0)
    return out


def _histogram_svg(values, bins: int = 20, width=720, height=140, pad=8) -> str:
    """Render a value distribution as SVG bars."""
    if not values:
        return f'<svg width="{width}" height="{height}"></svg>'
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in values:
        b = min(bins - 1, int((v - lo) / span * bins))
        counts[b] += 1
    cmax = max(counts) or 1
    bw = (width - 2 * pad) / bins
    rects = []
    for i, c in enumerate(counts):
        h = (c / cmax) * (height - 2 * pad)
        x = pad + i * bw
        y = height - pad - h
        rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 1:.1f}" height="{h:.1f}" fill="#3fb950"/>')
    return f'<svg width="{width}" height="{height}" style="background:#0d1117">{"".join(rects)}</svg>'


def write_tearsheet_html(path, result, title: str = "Backtest", timestamps=None, attribution=None) -> Path:
    """Write a self-contained HTML tearsheet for ``result`` and return its Path.

    ``timestamps`` (epoch ms, aligned to the equity curve) adds a monthly-returns
    table; ``attribution`` (``{symbol: pnl}``, e.g. ``PortfolioResult.per_symbol_pnl``)
    adds a per-asset attribution table.
    """
    eq = result.equity_curve
    stats = [
        ("Total Return", f"{metrics.total_return(eq) * 100:.2f}%"),
        ("Final Equity", f"{result.final_equity:,.2f}"),
        ("Sharpe", f"{metrics.sharpe(eq):.2f}"),
        ("Max Drawdown", f"{metrics.max_drawdown(eq) * 100:.2f}%"),
        ("Win Rate", f"{metrics.win_rate(result.trades) * 100:.1f}%"),
        ("Profit Factor", f"{metrics.profit_factor(result.trades):.2f}"),
        ("Trades", str(len(result.trades))),
    ]
    stat_rows = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in stats)
    trade_rows = "".join(
        f"<tr><td>{t.entry_price}</td><td>{t.exit_price}</td><td>{t.size}</td>"
        f"<td>{t.pnl:.2f}</td><td>{t.fees:.4f}</td></tr>"
        for t in result.trades[:1000]
    )

    monthly_html = ""
    mr = monthly_returns(timestamps, eq) if timestamps else []
    if mr:
        rows = "".join(f"<tr><td>{m}</td><td>{r * 100:.2f}%</td></tr>" for m, r in mr)
        monthly_html = f"<h2>Monthly Returns</h2><table><tr><th>Month</th><th>Return</th></tr>{rows}</table>"

    attribution_html = ""
    if attribution:
        rows = "".join(
            f"<tr><td>{sym}</td><td>{pnl:,.2f}</td></tr>"
            for sym, pnl in sorted(attribution.items(), key=lambda kv: kv[1], reverse=True)
        )
        attribution_html = f"<h2>Attribution (per asset)</h2><table><tr><th>Symbol</th><th>PnL</th></tr>{rows}</table>"

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{background:#0d1117;color:#c9d1d9;font:13px JetBrains Mono,monospace;margin:24px}}
h1{{color:#ff6a00;font-size:18px}} h2{{font-size:14px;border-bottom:1px solid #30363d;padding-bottom:4px}}
table{{border-collapse:collapse;margin:8px 0}} td,th{{border:1px solid #30363d;padding:4px 10px;text-align:right}}
td:first-child{{text-align:left}}
</style></head><body>
<h1>{title}</h1>
<h2>Equity</h2>{_polyline_svg(eq)}
<h2>Drawdown</h2>{_polyline_svg(_drawdown_series(eq))}
<h2>Rolling Sharpe</h2>{_polyline_svg(_rolling_sharpe(eq))}
<h2>Return Distribution</h2>{_histogram_svg(_bar_returns(eq))}
<h2>Stats</h2><table>{stat_rows}</table>
{monthly_html}
{attribution_html}
<h2>Trades</h2><table>
<tr><th>Entry</th><th>Exit</th><th>Size</th><th>PnL</th><th>Fees</th></tr>{trade_rows}</table>
</body></html>
"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html)
    return p
