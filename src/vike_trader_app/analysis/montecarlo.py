"""Monte Carlo resampling of backtest trade P&L.

Characterises outcome dispersion by building many synthetic equity paths from
the observed trade list.  All randomness uses a seeded ``random.Random`` so
results are fully deterministic given the same seed.

``significance._percentile`` is reused for percentile calculations.
"""

import random

from vike_trader_app.analysis.significance import _percentile


def _build_curve(start_equity: float, pnls: list[float]) -> list[float]:
    """Equity curve from start_equity + cumulative pnl list."""
    curve = [start_equity]
    eq = start_equity
    for p in pnls:
        eq += p
        curve.append(eq)
    return curve


def _max_drawdown_curve(curve: list[float]) -> float:
    """Max drawdown (positive fraction) of an equity curve; 0.0 if curve is monotone."""
    if not curve:
        return 0.0
    peak = curve[0]
    worst = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def mc_resample(
    trade_pnls,
    *,
    start_equity: float,
    n_sims: int = 1000,
    seed: int = 0,
    method: str = "shuffle",
) -> dict:
    """Run ``n_sims`` Monte Carlo paths over the trade P&L list.

    Parameters
    ----------
    trade_pnls:
        Iterable of trade P&L values (floats).
    start_equity:
        Starting portfolio equity for each path.
    n_sims:
        Number of simulation paths to run.
    seed:
        RNG seed — ensures determinism.
    method:
        ``'shuffle'`` reorders the trade list; ``'bootstrap'`` samples with
        replacement.  Both produce ``n_sims`` equity paths.

    Returns
    -------
    dict with keys:
        ``'terminal'``     — list of final equity values (length ``n_sims``).
        ``'max_drawdowns'``— list of per-path max drawdown fractions.
        ``'curves_sample'``— a small representative sample of full equity curves
                             (up to 20 paths), useful for plotting.
    """
    if method not in ("shuffle", "bootstrap"):
        raise ValueError(f"method must be 'shuffle' or 'bootstrap', got {method!r}")

    pnls = list(trade_pnls)
    rng = random.Random(seed)
    n = len(pnls)

    terminals: list[float] = []
    max_drawdowns: list[float] = []
    all_curves: list[list[float]] = []

    for _ in range(n_sims):
        if method == "shuffle":
            rng.shuffle(pnls)
            resampled = list(pnls)
        else:  # bootstrap
            resampled = [pnls[rng.randrange(n)] for _ in range(n)] if n > 0 else []

        curve = _build_curve(start_equity, resampled)
        terminals.append(curve[-1])
        max_drawdowns.append(_max_drawdown_curve(curve))
        all_curves.append(curve)

    # Thin down curves_sample to at most 20 paths spread evenly across sims
    step = max(1, n_sims // 20)
    curves_sample = all_curves[::step][:20]

    return {
        "terminal": terminals,
        "max_drawdowns": max_drawdowns,
        "curves_sample": curves_sample,
    }


def confidence_bands(
    curves: list[list[float]],
    qs: tuple = (0.05, 0.50, 0.95),
) -> dict:
    """Per-step percentile bands across equal-length simulation curves.

    Parameters
    ----------
    curves:
        List of equity-curve lists, all the same length.
    qs:
        Quantiles to compute (default 5th, 50th, 95th percentile).

    Returns
    -------
    dict mapping each quantile ``q`` to a list of per-step values.
    """
    if not curves:
        return {q: [] for q in qs}
    length = len(curves[0])
    result = {q: [] for q in qs}
    for step in range(length):
        col = sorted(c[step] for c in curves if step < len(c))
        for q in qs:
            result[q].append(_percentile(col, q))
    return result


def risk_of_ruin(terminal_equities: list[float], ruin_level: float) -> float:
    """Fraction of simulation paths that end at or below ``ruin_level``."""
    if not terminal_equities:
        return 0.0
    below = sum(1 for e in terminal_equities if e <= ruin_level)
    return below / len(terminal_equities)


def mc_summary(
    trade_pnls,
    *,
    start_equity: float,
    n_sims: int = 1000,
    seed: int = 0,
    ruin_pct: float = 0.5,
) -> dict:
    """Run Monte Carlo and return key summary statistics.

    Parameters
    ----------
    trade_pnls:
        Iterable of trade P&L values.
    start_equity:
        Starting equity for each path.
    n_sims:
        Number of simulation paths.
    seed:
        RNG seed.
    ruin_pct:
        Ruin is defined as terminal equity <= ``ruin_pct * start_equity``.

    Returns
    -------
    dict with keys:
        ``'terminal_p5'``, ``'terminal_p50'``, ``'terminal_p95'`` — percentiles of terminal equity.
        ``'max_dd_p50'``, ``'max_dd_p95'`` — percentiles of per-path max drawdown.
        ``'prob_loss'`` — fraction of paths ending below ``start_equity``.
        ``'risk_of_ruin'`` — fraction of paths ending <= ``ruin_pct * start_equity``.
    """
    result = mc_resample(
        trade_pnls,
        start_equity=start_equity,
        n_sims=n_sims,
        seed=seed,
        method="shuffle",
    )
    terminals = sorted(result["terminal"])
    mdd_sorted = sorted(result["max_drawdowns"])

    ruin_level = ruin_pct * start_equity

    return {
        "terminal_p5": _percentile(terminals, 0.05),
        "terminal_p50": _percentile(terminals, 0.50),
        "terminal_p95": _percentile(terminals, 0.95),
        "max_dd_p50": _percentile(mdd_sorted, 0.50),
        "max_dd_p95": _percentile(mdd_sorted, 0.95),
        "prob_loss": risk_of_ruin(terminals, start_equity),
        "risk_of_ruin": risk_of_ruin(terminals, ruin_level),
    }
