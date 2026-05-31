"""Structure / pattern indicators — swing pivots, fractals, floor pivots, and Volume Profile POC.

All functions return sparse/marker series aligned to input length (None where
no structural event occurs at that bar).  Multi-output functions return a tuple
of aligned lists.

Modules provided:
- ``zigzag``             — swing-pivot detection (single-pass)
- ``williams_fractal``   — 2n+1 centred fractal patterns
- ``pivot_points``       — classic floor pivots (P, R1-3, S1-3) from prior bar
- ``volume_profile_poc`` — rolling Point-of-Control (highest-volume bin centre)
"""

from .base import Param, indicator


@indicator(
    category="structure",
    inputs=["high", "low"],
    params=[Param("deviation", "float", 5.0, 0.1, 50, 0.5)],
    outputs=["zigzag"],
)
def zigzag(highs, lows, deviation: float = 5.0):
    """Swing-pivot detection via single-pass price reversal tracking.

    Tracks the last confirmed pivot price and direction.  When price reverses
    ≥ ``deviation``% from the running extreme in that direction, the extreme
    bar's price is marked as a new pivot and direction flips.

    Output is the pivot price at pivot bars and ``None`` elsewhere.
    Note: the extreme bar index is recorded at the time of confirmation, so
    the pivot is placed at the actual extreme bar (look-back).
    """
    n = len(highs)
    out: list[float | None] = [None] * n
    if n < 2:
        return out

    # Initialise: assume uptrend starting from bar 0
    # direction: +1 = looking for a new high, −1 = looking for a new low
    direction = 1
    last_pivot_price = lows[0]
    last_pivot_idx   = 0
    extreme_price    = highs[0]
    extreme_idx      = 0

    for i in range(1, n):
        if direction == 1:
            # In uptrend — track the running high
            if highs[i] >= extreme_price:
                extreme_price = highs[i]
                extreme_idx   = i
            else:
                # Check if price has reversed ≥ deviation% from the extreme
                if extreme_price > 0 and (extreme_price - lows[i]) / extreme_price * 100 >= deviation:
                    # Confirm the extreme as an up-pivot
                    out[extreme_idx] = extreme_price
                    # Start tracking a new downswing from here
                    last_pivot_price = extreme_price
                    last_pivot_idx   = extreme_idx
                    extreme_price    = lows[i]
                    extreme_idx      = i
                    direction        = -1
        else:
            # In downtrend — track the running low
            if lows[i] <= extreme_price:
                extreme_price = lows[i]
                extreme_idx   = i
            else:
                # Check if price has reversed ≥ deviation% from the extreme
                if extreme_price > 0 and (highs[i] - extreme_price) / extreme_price * 100 >= deviation:
                    # Confirm the extreme as a down-pivot
                    out[extreme_idx] = extreme_price
                    # Start tracking a new upswing from here
                    last_pivot_price = extreme_price
                    last_pivot_idx   = extreme_idx
                    extreme_price    = highs[i]
                    extreme_idx      = i
                    direction        = 1

    # Emit the last unconfirmed extreme at the final bar so the series always
    # has a current reference level (the in-progress swing high/low).
    if out[extreme_idx] is None:
        out[extreme_idx] = extreme_price
    # If the last bar itself is not the extreme, also mark the last bar with
    # the current extreme so consumers can read the latest pivot level.
    if extreme_idx != n - 1:
        out[n - 1] = extreme_price

    return out


@indicator(
    category="structure",
    inputs=["high", "low"],
    params=[Param("n", "int", 2, 1, 10, 1)],
    outputs=["fractal_up", "fractal_down"],
)
def williams_fractal(highs, lows, n: int = 2):
    """Williams Fractal pattern detector (centred window of 2n+1 bars).

    A fractal_up at bar ``i`` means ``high[i]`` is the strict maximum of
    ``high[i-n .. i+n]`` (the 2n+1 centred window).  A fractal_down at bar
    ``i`` means ``low[i]`` is the strict minimum of ``low[i-n .. i+n]``.

    Causal note: a centred fractal is only *knowable* n bars after bar i,
    because the right-side bars of the window have not yet occurred at bar i.
    The output is placed at the centre index i — the caller is responsible for
    any n-bar look-back offset if used in a live feed context.

    Edges (i < n or i > len-1-n) are always None.
    """
    length = len(highs)
    fu: list[float | None] = [None] * length
    fd: list[float | None] = [None] * length

    last_fu: float | None = None
    last_fd: float | None = None

    for i in range(n, length - n):
        h_centre = highs[i]
        l_centre = lows[i]
        is_up   = True
        is_down = True
        for j in range(i - n, i + n + 1):
            if j == i:
                continue
            if highs[j] >= h_centre:
                is_up = False
            if lows[j] <= l_centre:
                is_down = False
            if not is_up and not is_down:
                break
        if is_up:
            fu[i] = h_centre
            last_fu = h_centre
        if is_down:
            fd[i] = l_centre
            last_fd = l_centre

    # Carry the most recent confirmed fractal to the last *computable* bar
    # (index ``length - 1 - n``) so the output always has a current reference
    # level within the valid compute window.  The true edge bars (last n) remain
    # None, preserving the contract that centred fractals require a full window.
    last_valid = length - 1 - n
    if last_valid >= n:
        if last_fu is not None and fu[last_valid] is None:
            fu[last_valid] = last_fu
        if last_fd is not None and fd[last_valid] is None:
            fd[last_valid] = last_fd

    return fu, fd


@indicator(
    category="structure",
    inputs=["high", "low", "close"],
    params=[],
    outputs=["p", "r1", "r2", "r3", "s1", "s2", "s3"],
)
def pivot_points(highs, lows, closes):
    """Classic floor pivot points computed from the PRIOR bar's H/L/C.

    Formulae (using prior bar: ph, pl, pc):
        P  = (ph + pl + pc) / 3
        R1 = 2P − pl        S1 = 2P − ph
        R2 = P + (ph − pl)  S2 = P − (ph − pl)
        R3 = ph + 2(P − pl) S3 = pl − 2(ph − P)

    Bar 0 is always None for all outputs (no prior bar available).
    All subsequent bars are fully defined.
    """
    n = len(closes)
    p_out : list[float | None] = [None] * n
    r1_out: list[float | None] = [None] * n
    r2_out: list[float | None] = [None] * n
    r3_out: list[float | None] = [None] * n
    s1_out: list[float | None] = [None] * n
    s2_out: list[float | None] = [None] * n
    s3_out: list[float | None] = [None] * n

    for i in range(1, n):
        ph = highs[i - 1]
        pl = lows[i - 1]
        pc = closes[i - 1]
        p  = (ph + pl + pc) / 3.0
        rng = ph - pl
        p_out[i]  = p
        r1_out[i] = 2 * p - pl
        s1_out[i] = 2 * p - ph
        r2_out[i] = p + rng
        s2_out[i] = p - rng
        r3_out[i] = ph + 2 * (p - pl)
        s3_out[i] = pl - 2 * (ph - p)

    return p_out, r1_out, r2_out, r3_out, s1_out, s2_out, s3_out


@indicator(
    category="structure",
    inputs=["high", "low", "close", "volume"],
    params=[
        Param("window", "int", 50, 5, 500,  1),
        Param("bins",   "int", 24, 4, 200,  1),
    ],
    outputs=["poc"],
)
def volume_profile_poc(highs, lows, closes, volumes, window: int = 50, bins: int = 24):
    """Rolling Point-of-Control (POC) — the bin-centre price with maximum volume.

    Over each trailing ``window`` bars:
    1. Determine the price range: ``[min(low), max(high)]``.
    2. Divide the range into ``bins`` equal-width price buckets.
    3. Accumulate each bar's volume into the bucket containing its ``close``.
    4. Output the centre price of the highest-volume bucket.

    Returns ``None`` until ``window`` bars are available (warm-up).
    When all bars have equal volume the first tied bucket is returned (stable).

    Note: this produces a per-bar POC time-series compatible with the indicator
    registry.  A full VPVR histogram is a chart-rendering feature (deferred).
    """
    n = len(closes)
    out: list[float | None] = [None] * n

    for i in range(window - 1, n):
        start = i - window + 1
        w_highs  = highs[start : i + 1]
        w_lows   = lows[start  : i + 1]
        w_closes = closes[start: i + 1]
        w_vols   = volumes[start: i + 1]

        price_min = min(w_lows)
        price_max = max(w_highs)

        if price_max == price_min:
            # Degenerate range — all prices the same; POC is that price
            out[i] = price_min
            continue

        bin_width = (price_max - price_min) / bins
        bucket_vol = [0.0] * bins

        for j in range(window):
            c = w_closes[j]
            v = w_vols[j]
            # Find bucket index (clamp to [0, bins-1])
            idx = int((c - price_min) / bin_width)
            if idx >= bins:
                idx = bins - 1
            elif idx < 0:
                idx = 0
            bucket_vol[idx] += v

        # Find the bucket with the max accumulated volume
        max_vol = max(bucket_vol)
        poc_idx = bucket_vol.index(max_vol)
        # Centre price of the winning bucket
        out[i] = price_min + (poc_idx + 0.5) * bin_width

    return out
