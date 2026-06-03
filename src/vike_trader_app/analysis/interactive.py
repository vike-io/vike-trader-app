"""Optional interactive (Plotly) HTML tearsheet — ``vike_trader_app[viz]`` extra.

The default ``tearsheet.write_tearsheet_html`` emits self-contained static SVG
(tiny, offline, dependency-free). This adds a richer *interactive* report (hover,
zoom, pan) for when that's worth a Plotly dependency. Inline by default so the file
stays self-contained and works offline.
"""

from pathlib import Path

from . import metrics
from ..ui import theme


def write_interactive_html(path, result, title: str = "Backtest", timestamps=None, inline: bool = True) -> Path:
    """Write an interactive Plotly tearsheet (equity + drawdown). Returns its Path."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise RuntimeError("write_interactive_html requires the optional extra: pip install vike_trader_app[viz]") from exc

    eq = result.equity_curve
    peak = eq[0] if eq else 0.0
    dd = []
    for v in eq:
        peak = max(peak, v)
        dd.append((v - peak) / peak * 100 if peak else 0.0)
    x = timestamps if (timestamps and len(timestamps) == len(eq)) else list(range(len(eq)))

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3],
        subplot_titles=("Equity", "Drawdown %"),
    )
    fig.add_trace(go.Scatter(x=x, y=eq, name="equity", line={"color": theme.ACCENT}), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=dd, name="drawdown", fill="tozeroy", line={"color": theme.DOWN}), row=2, col=1)
    fig.update_layout(
        title=f"{title}  ·  return {metrics.total_return(eq) * 100:.2f}%  ·  Sharpe {metrics.sharpe(eq):.2f}",
        template="plotly_dark", paper_bgcolor=theme.BG, plot_bgcolor=theme.BG,
        font={"family": "JetBrains Mono, monospace", "color": theme.TEXT}, showlegend=False,
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(p), include_plotlyjs=True if inline else "cdn")
    return p
