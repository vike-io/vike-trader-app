"""Qt-free helpers that prepare engine output for plotting."""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Marker:
    """A point to draw on the price chart."""

    ts: int
    price: float
    kind: str  # "entry" | "exit"


def trade_markers(trades) -> list[Marker]:
    """Two markers per trade: the entry fill and the exit fill."""
    markers: list[Marker] = []
    for t in trades:
        markers.append(Marker(ts=t.entry_ts, price=t.entry_price, kind="entry"))
        markers.append(Marker(ts=t.exit_ts, price=t.exit_price, kind="exit"))
    return markers


def equity_points(
    timestamps: list[int], equity_curve: list[float]
) -> tuple[list[int], list[float]]:
    """Zip bar timestamps with the per-bar equity curve (lengths must match)."""
    if len(timestamps) != len(equity_curve):
        raise ValueError(
            f"length mismatch: {len(timestamps)} timestamps vs {len(equity_curve)} equity points"
        )
    return list(timestamps), list(equity_curve)


def initial_window(n_total: int, window: int) -> tuple[int, int]:
    """The default visible x-range: the last ``window`` bars (full range if fewer)."""
    if n_total <= 0:
        return (0, 0)
    return (max(0, n_total - window), n_total)


def follow_window(index: int, n_total: int, window: int) -> tuple[int, int]:
    """A ``window``-wide x-range that keeps the replay cursor ``index`` in view."""
    hi = min(n_total, index + max(1, window // 5))
    lo = max(0, hi - window)
    return (lo, hi)


def y_bounds(bars, lo: int, hi: int):
    """Min low / max high over ``bars[lo:hi]`` for auto-fitting the visible price, or None."""
    visible = bars[lo:hi]
    if not visible:
        return None
    return (min(b.low for b in visible), max(b.high for b in visible))


# --- timestamp <-> bar-index mapping + axis/legend formatting (TradingView-style chrome) ---

def bar_spacing(bars) -> int:
    """Bar spacing in ms (first gap); 0 if fewer than 2 bars."""
    return (bars[1].ts - bars[0].ts) if len(bars) >= 2 else 0


def ts_to_x(bars, ts: int) -> float:
    """Epoch-ms timestamp -> fractional bar-index x. 0.0 when unmappable."""
    if not bars:
        return 0.0
    sp = bar_spacing(bars)
    return (ts - bars[0].ts) / sp if sp > 0 else 0.0


def x_to_ts(bars, x: float) -> int:
    """Fractional bar-index x -> epoch-ms timestamp (extrapolates outside range)."""
    if not bars:
        return int(x)
    sp = bar_spacing(bars)
    return int(round(bars[0].ts + x * sp)) if sp > 0 else bars[0].ts


def axis_time_label(bars, index) -> str:
    """Format the time at integer bar ``index`` (UTC 'MM-DD HH:MM'); '' if no bars."""
    if not bars:
        return ""
    i = int(round(index))
    ts = bars[i].ts if 0 <= i < len(bars) else x_to_ts(bars, index)
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def ohlc_legend_text(bar, prev_close=None) -> str:
    """'O.. H.. L.. C.. +chg (chg%)' header text; '' when bar is None."""
    if bar is None:
        return ""
    parts = [f"O{bar.open:g}", f"H{bar.high:g}", f"L{bar.low:g}", f"C{bar.close:g}"]
    if prev_close:
        chg = bar.close - prev_close
        pct = chg / prev_close * 100
        s = "+" if chg >= 0 else ""
        parts.append(f"{s}{chg:g} ({s}{pct:.2f}%)")
    return "  ".join(parts)
