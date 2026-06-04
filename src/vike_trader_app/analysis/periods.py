"""Calendar-period returns and drawdown table — pure analytics, no UI dependency.

``periodic_returns``:  Group an equity curve into calendar buckets and compute
    the period return for each bucket.

``monthly_return_matrix``:  Produce the year x month heatmap data structure.

``drawdown_table``:  Find the top-N drawdown episodes with peak/trough/recovery
    timestamps and bar-counts.
"""

from __future__ import annotations

from datetime import datetime, timezone


def _epoch_ms_to_utc(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)


def _period_label(dt: datetime, period: str) -> str:
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    if period == "weekly":
        # ISO year + week number
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "monthly":
        return f"{dt.year}-{dt.month:02d}"
    if period == "yearly":
        return str(dt.year)
    raise ValueError(f"period must be 'daily','weekly','monthly','yearly'; got {period!r}")


def period_key(ts_ms: int, period: str) -> str:
    """A comparable label for the calendar period containing ``ts_ms`` (UTC).

    Parameters
    ----------
    ts_ms:
        Epoch-millisecond timestamp.
    period:
        One of ``'daily'``, ``'weekly'``, ``'monthly'``, ``'quarterly'``, ``'yearly'``.

    Returns
    -------
    A string label:
        daily      -> ``'2024-03-15'``
        weekly     -> ``'2024-W11'`` (ISO week)
        monthly    -> ``'2024-03'``
        quarterly  -> ``'2024-Q1'``
        yearly     -> ``'2024'``

    Raises
    ------
    ValueError
        If ``period`` is not one of the supported values.
    """
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    if period == "weekly":
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    if period == "monthly":
        return f"{dt.year}-{dt.month:02d}"
    if period == "quarterly":
        q = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{q}"
    if period == "yearly":
        return str(dt.year)
    raise ValueError(
        f"period must be 'daily','weekly','monthly','quarterly','yearly'; got {period!r}"
    )


def periodic_returns(
    equity_curve: list[float],
    timestamps: list[int],
    period: str = "monthly",
) -> list[tuple[str, float]]:
    """Return (label, period_return) tuples grouped by calendar period.

    Parameters
    ----------
    equity_curve:
        Equity values at each bar.
    timestamps:
        Epoch-millisecond timestamps, same length as ``equity_curve``.
    period:
        One of ``'daily'``, ``'weekly'``, ``'monthly'``, ``'yearly'``.

    Returns
    -------
    List of ``(label, return_fraction)`` in chronological order.
    ``return_fraction = last_equity_in_period / first_equity_at_start_of_period - 1``.
    """
    if len(equity_curve) != len(timestamps):
        raise ValueError("equity_curve and timestamps must have equal length")
    if not equity_curve:
        return []

    # Validate period early (raises ValueError for unknown values)
    _period_label(_epoch_ms_to_utc(timestamps[0]), period)

    # Group by label, keeping the FIRST equity before the period starts and the
    # LAST equity in the period.  We need the equity value *at the open of the
    # period* (i.e. the equity at the last bar of the previous period), so we
    # track the last-seen equity as we iterate.
    groups: dict[str, tuple[float, float]] = {}  # label -> (first_equity, last_equity)
    label_order: list[str] = []

    # The "entry" equity for the first period is equity_curve[0] itself.
    prev_label: str | None = None
    prev_equity = equity_curve[0]

    for i, (eq, ts) in enumerate(zip(equity_curve, timestamps)):
        dt = _epoch_ms_to_utc(ts)
        lbl = _period_label(dt, period)

        if lbl not in groups:
            # First bar of this new period — entry equity is the equity from
            # the previous bar (or equity_curve[0] if this is the very first).
            entry_eq = prev_equity if prev_label is not None else equity_curve[0]
            groups[lbl] = (entry_eq, eq)
            label_order.append(lbl)
        else:
            # Update the last-seen equity for this label
            entry_eq, _ = groups[lbl]
            groups[lbl] = (entry_eq, eq)

        prev_label = lbl
        prev_equity = eq

    result: list[tuple[str, float]] = []
    for lbl in label_order:
        entry_eq, last_eq = groups[lbl]
        if entry_eq == 0:
            ret = 0.0
        else:
            ret = last_eq / entry_eq - 1.0
        result.append((lbl, ret))

    return result


def monthly_return_matrix(
    equity_curve: list[float],
    timestamps: list[int],
) -> dict:
    """Build a year x month heatmap of returns.

    Returns
    -------
    dict with keys:
        ``'years'``  — sorted list of integer years present.
        ``'matrix'`` — ``{year: {month(1..12): return | None}}``
        ``'annual'`` — ``{year: annual_return_fraction}``
    """
    monthly = periodic_returns(equity_curve, timestamps, period="monthly")

    # Parse the "YYYY-MM" labels back to (year, month)
    matrix: dict[int, dict[int, float | None]] = {}
    for label, ret in monthly:
        year, month = int(label[:4]), int(label[5:7])
        matrix.setdefault(year, {})[month] = ret

    # Compute annual returns by compounding monthly returns within each year
    annual: dict[int, float] = {}
    for year, months in matrix.items():
        product = 1.0
        for m in range(1, 13):
            r = months.get(m)
            if r is not None:
                product *= 1.0 + r
        annual[year] = product - 1.0

    years = sorted(matrix.keys())
    return {
        "years": years,
        "matrix": matrix,
        "annual": annual,
    }


def drawdown_table(
    equity_curve: list[float],
    timestamps: list[int],
    top_n: int = 5,
) -> list[dict]:
    """Find the top-N drawdown episodes sorted by depth (worst first).

    Each entry is a dict:
        ``depth``       — fractional drawdown from peak (positive, e.g. 0.20 = 20 %).
        ``peak_ts``     — epoch-ms timestamp of the peak bar.
        ``trough_ts``   — epoch-ms timestamp of the deepest bar in the episode.
        ``recovery_ts`` — epoch-ms timestamp where equity returns to ≥ peak, or
                          ``None`` if never recovered within the curve.
        ``length``      — number of bars from peak to trough (inclusive).
        ``recovery``    — number of bars from trough to recovery (inclusive), or
                          ``None`` if not recovered.
    """
    if len(equity_curve) != len(timestamps):
        raise ValueError("equity_curve and timestamps must have equal length")
    if not equity_curve:
        return []

    # Walk the curve and identify all drawdown episodes.
    # An episode starts when the equity falls below the running peak, ends when
    # it recovers to (>=) that peak or the series ends.

    n = len(equity_curve)
    peak_val = equity_curve[0]
    peak_idx = 0
    in_drawdown = False
    trough_val = equity_curve[0]
    trough_idx = 0
    episodes: list[dict] = []

    def _close_episode(recovery_idx: int | None) -> dict:
        depth = (peak_val - trough_val) / peak_val if peak_val != 0 else 0.0
        length = trough_idx - peak_idx
        recovery_bars = (recovery_idx - trough_idx) if recovery_idx is not None else None
        return {
            "depth": depth,
            "peak_ts": timestamps[peak_idx],
            "trough_ts": timestamps[trough_idx],
            "recovery_ts": timestamps[recovery_idx] if recovery_idx is not None else None,
            "length": length,
            "recovery": recovery_bars,
        }

    for i in range(n):
        eq = equity_curve[i]
        if eq >= peak_val:
            if in_drawdown and trough_val < peak_val:
                # Recovered — close the current episode
                ep = _close_episode(recovery_idx=i)
                if ep["depth"] > 0:
                    episodes.append(ep)
            # Reset peak to this new high
            peak_val = eq
            peak_idx = i
            trough_val = eq
            trough_idx = i
            in_drawdown = False
        else:
            in_drawdown = True
            if eq < trough_val:
                trough_val = eq
                trough_idx = i

    # Close any open episode at end of series (never recovered)
    if in_drawdown and trough_val < peak_val:
        ep = _close_episode(recovery_idx=None)
        if ep["depth"] > 0:
            episodes.append(ep)

    # Sort by depth descending, return top_n
    episodes.sort(key=lambda e: e["depth"], reverse=True)
    return episodes[:top_n]
