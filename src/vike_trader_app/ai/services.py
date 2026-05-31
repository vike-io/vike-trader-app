"""AI service layer — pure, JSON-friendly wrappers over the engine/optimizer/overfit code.

These functions are designed to be called by an MCP server or CLI chat interface.
All inputs and outputs are plain Python types (dicts, lists, floats, ints, strings)
so they serialize cleanly to JSON without any additional transformation.
"""

from __future__ import annotations

import numpy as np

from ..core.model import Bar
from ..core.vectorized import _sma_matrix, _cross_signals, sweep_sma_cross
from ..core.fastsim import fast_backtest
from ..analysis import metrics as _metrics
from ..analysis.overfit import deflated_sharpe_ratio, pbo_cscv, overfit_verdict
from ..data.cache import get_bars


# ---------------------------------------------------------------------------
# Task 1: bars_to_data + fetch_ohlcv
# ---------------------------------------------------------------------------


def bars_to_data(bars: list[Bar]) -> dict:
    """Convert a list of Bar objects to a plain-dict of parallel lists.

    Returns keys: open, high, low, close, ts, funding.
    Funding defaults to 0.0 when the bar has no funding rate.
    """
    return {
        "open": [b.open for b in bars],
        "high": [b.high for b in bars],
        "low": [b.low for b in bars],
        "close": [b.close for b in bars],
        "ts": [b.ts for b in bars],
        "funding": [b.funding if b.funding is not None else 0.0 for b in bars],
    }


def _summarize_bars(symbol: str, interval: str, bars: list[Bar]) -> dict:
    """Build the fetch_ohlcv summary dict from the fetched bars."""
    return {
        "symbol": symbol,
        "interval": interval,
        "n_bars": len(bars),
        "first_ts": bars[0].ts if bars else None,
        "last_ts": bars[-1].ts if bars else None,
        "closes": [b.close for b in bars],
    }


def fetch_ohlcv(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: int,
    source: str = "binance",
    fetcher=None,
    root: str = "storage/parquet",
    progress=None,
) -> dict:
    """Fetch OHLCV bars and return a JSON-friendly summary dict.

    When ``fetcher`` is provided it is called directly (useful for testing and
    for injecting alternative data sources). Otherwise ``data.cache.get_bars``
    is used with the Binance fetcher.

    Returns a dict with: symbol, interval, n_bars, first_ts, last_ts, closes.
    """
    if fetcher is not None:
        bars = fetcher(symbol, interval, start_ms, end_ms, progress=progress)
    else:
        bars = get_bars(
            symbol, interval, start_ms, end_ms, root=root, progress=progress
        )
    return _summarize_bars(symbol, interval, bars)


# ---------------------------------------------------------------------------
# run_sma_backtest
# ---------------------------------------------------------------------------


def run_sma_backtest(closes, fast: int, slow: int, *, fee_rate: float = 0.0,
                     init_cash: float = 10_000.0) -> dict:
    """Backtest an SMA(fast)x SMA(slow) crossover (long/flat) on ``closes``; return full metrics.

    ``closes`` is a list of float close prices (JSON-friendly — an LLM/MCP client passes these
    directly). Builds entry/exit signals with the vectorized SMA helpers, runs the compiled
    kernel for the equity curve + trades, then computes the metric suite (incl. Sortino/Calmar).
    """
    closes = list(map(float, closes))
    n = len(closes)
    smas = _sma_matrix(np.asarray(closes, dtype=float), [fast, slow])
    entries, exits = _cross_signals(smas[fast], smas[slow])
    opens = [closes[0]] + closes[:-1] if n else []
    ts = list(range(n))
    funding = [0.0] * n
    res = fast_backtest(
        opens, closes, closes, closes, funding, ts,
        entries, exits, [1.0] * n, [1] * n,
        taker_fee=fee_rate, init_cash=init_cash,
    )
    eq = res["equity_curve"]
    trades = res["trades"]
    return {
        "params": {"fast": fast, "slow": slow},
        "total_return": _metrics.total_return(eq),
        "sharpe": _metrics.sharpe(eq),
        "sortino": _metrics.sortino(eq),
        "calmar": _metrics.calmar(eq),
        "max_drawdown": _metrics.max_drawdown(eq),
        "win_rate": _metrics.win_rate(trades),
        "profit_factor": _metrics.profit_factor(trades),
        "n_trades": int(res["n_trades"]),
        "final_equity": float(res["final_equity"]),
    }


# ---------------------------------------------------------------------------
# optimize_sma
# ---------------------------------------------------------------------------


def optimize_sma(closes, fasts, slows, *, fee_rate: float = 0.0, top_n: int = 10) -> dict:
    """Sweep an SMA crossover over the ``fasts`` x ``slows`` grid; return the top-N ranked combos.

    ``closes`` is a list of float close prices. Each result carries ``params`` + ``total_return``
    + ``n_trades``. ``top_n`` bounds the returned list (the full grid is always swept).
    """
    results = sweep_sma_cross(list(map(float, closes)), list(fasts), list(slows), fee_rate=fee_rate)
    return {
        "n_combos": len(results),
        "top": results[:top_n],
    }


# ---------------------------------------------------------------------------
# overfit_check
# ---------------------------------------------------------------------------


def overfit_check(observed_sr: float, trial_sharpes, n_obs: int, *,
                  pbo_matrix=None, n_splits: int = 4, wf_consistency: float | None = None) -> dict:
    """Anti-overfitting summary: Deflated Sharpe (+ optional PBO via CSCV) + a verdict.

    ``trial_sharpes`` are the Sharpes of all configurations tried (for the deflation). Pass
    ``pbo_matrix`` (T observations x N trials of per-observation performance) to also compute a
    real PBO; without it ``pbo`` is 0.0 (no fabricated matrix).
    """
    dsr = deflated_sharpe_ratio(observed_sr, list(trial_sharpes), n_obs)
    pbo = pbo_cscv(pbo_matrix, n_splits) if pbo_matrix is not None else 0.0
    verdict = overfit_verdict(pbo, dsr, wf_consistency)
    return {
        "deflated_sharpe": float(dsr),
        "pbo": float(pbo),
        "verdict": {"level": verdict.level, "reasons": list(verdict.reasons)},
    }


def list_indicators(category: str | None = None) -> dict:
    """List available technical indicators with their parameter metadata (for discovery)."""
    from ..core.indicators import base

    specs = base.list_indicators(category)
    return {"n": len(specs), "indicators": [base.describe(s.name) for s in specs]}


def compute_indicator(name: str, ohlcv: dict, params: dict | None = None) -> dict:
    """Compute indicator ``name`` over an OHLCV column dict; return its named output series.

    ``ohlcv`` maps open/high/low/close/volume (and ``benchmark`` for beta/correl) to aligned lists.
    ``params`` overrides indicator parameters (registry defaults are used otherwise).
    """
    from ..core.indicators import base

    spec = base.get(name)
    out = base.compute(name, ohlcv, **(params or {}))
    series = out if isinstance(out, tuple) else (out,)
    return {"name": name, "outputs": {o: list(s) for o, s in zip(spec.outputs, series)}}


def query_kb(query: str, k: int = 5, *, kb=None, embedder=None) -> dict:
    """Search the project knowledge base; return the top-k passages.

    ``kb``/``embedder`` are injectable (tests pass a stub). By default a ``FastEmbedEmbedder`` and
    the package's prebuilt/lazily-built index are used (requires the ``[ai]`` extra).
    """
    from .knowledge import FastEmbedEmbedder, default_knowledge_base

    if embedder is None:
        embedder = FastEmbedEmbedder()
    if kb is None:
        kb = default_knowledge_base(embedder)
    hits = kb.query(query, k=k, embedder=embedder)
    return {"n": len(hits), "hits": hits}
