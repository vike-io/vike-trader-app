"""Qt-free helpers that prepare engine output for plotting."""

from dataclasses import dataclass


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
