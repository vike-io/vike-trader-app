"""Qt-free helpers that prepare engine output for plotting."""

from datetime import datetime, timezone


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


def series_slice(series, index: int) -> tuple[list[int], list]:
    """An indicator/overlay series revealed up to (and including) bar ``index``: the x indices with
    a non-None value and their y values. Shared by every chart reveal path so the None-filter +
    index-cap logic lives in one Qt-free, unit-testable place."""
    xs = [k for k in range(min(index + 1, len(series))) if series[k] is not None]
    return xs, [series[k] for k in xs]


def oscillator_reveal(inds, labels_by_uid, index: int, win_lo: int, win_hi: int, ma_key: str = "MA"):
    """Pure compute for an OscillatorPane reveal (the Qt-free seam under OscillatorPane.reveal).

    For each indicator (objects exposing ``.uid``, ``.series`` {label: list}, ``.shown`` bool,
    ``.bands`` [(label, value)]) and the curve labels active for it (``labels_by_uid[uid]``):
    the revealed ``(xs, ys)`` per label, the legend "last" value (the base output's last y, NOT the
    smoothing-MA ``ma_key``), and the y-range fitted to the visible ``[win_lo, win_hi]`` window
    (falling back to the full revealed series when nothing is in-window yet). Band threshold values
    extend the range (extend-only, like the dashed guides). Returns
    ``(plots {uid: {label: (xs, ys)}}, lasts {uid: last|None}, y_range (lo, hi)|None)``.
    """
    plots, lasts = {}, {}
    win_ys: list = []
    full_ys: list = []
    for ind in inds:
        per = {}
        last = None
        for label in labels_by_uid.get(ind.uid, []):
            series = ind.series.get(label, [])
            xs, ys = series_slice(series, index)
            per[label] = (xs, ys)
            if ind.shown:
                full_ys += ys
                win_ys += [series[k] for k in xs if win_lo <= k <= win_hi]
            if ys and label != ma_key:   # legend value stays on the base output, not the MA
                last = ys[-1]
        if ind.shown:
            band_vals = [float(val) for _lbl, val in getattr(ind, "bands", [])]
            win_ys += band_vals
            full_ys += band_vals
        plots[ind.uid] = per
        lasts[ind.uid] = last
    all_ys = win_ys or full_ys   # fall back to the full series if nothing is in-window yet
    y_range = None
    if all_ys:
        lo, hi = min(all_ys), max(all_ys)
        if hi > lo:
            y_range = (lo, hi)
    return plots, lasts, y_range


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
    """Wall-clock label for a (possibly fractional) bar-index tick: 'HH:MM' intraday,
    'Mon DD' at a midnight boundary (TradingView-style). Uses interpolated time so ticks
    placed on round wall-clock boundaries read as round times. '' if no bars."""
    if not bars:
        return ""
    ts = x_to_ts(bars, index)
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%b %d") if (dt.hour == 0 and dt.minute == 0) else dt.strftime("%H:%M")


def price_decimals(ref: float) -> int:
    """Decimal places for a price of this magnitude — BTC/indices 2, sub-$ forex up to 6."""
    a = abs(ref)
    return 2 if a >= 100 else 4 if a >= 1 else 6


def fmt_price(v: float, ref: float | None = None) -> str:
    """Price with thousands separators + magnitude-scaled decimals (TradingView look):
    ``73,182.49`` for BTC, ``1.1650`` for forex. ``ref`` fixes the precision to another
    value's magnitude (so a +35.36 change next to a 73k price still shows 2 dp)."""
    return f"{v:,.{price_decimals(v if ref is None else ref)}f}"
