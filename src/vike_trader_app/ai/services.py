"""AI service layer — pure, JSON-friendly wrappers over the engine/optimizer/overfit code.

These functions are designed to be called by an MCP server or CLI chat interface.
All inputs and outputs are plain Python types (dicts, lists, floats, ints, strings)
so they serialize cleanly to JSON without any additional transformation.
"""

from __future__ import annotations

import os

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


# ---------------------------------------------------------------------------
# Strategy-class tools: the real engine surface (run / optimize / walk-forward
# / portfolio / scanner). These let an agent (Claude Code / Desktop over MCP)
# drive the SAME StrategyTester / PortfolioStrategyTester / screener the GUI
# uses. Inputs/outputs are plain JSON types. Bars load from the local Parquet
# cache on the calling thread — safe here because the MCP server is a headless,
# single-threaded process (no Qt event loop).
# ---------------------------------------------------------------------------

_DATA_ROOT = os.environ.get("VIKE_DATA_ROOT") or "storage/parquet"

# TesterConfig fields that are safe to set from a JSON payload.
_CONFIG_KEYS = (
    "cash", "fee_rate", "maker_fee", "taker_fee", "slippage", "multiplier",
    "leverage", "maint_margin", "max_open_long", "max_open_short",
    "volume_limit", "periods_per_year",
)

# Headline metrics returned per report/trial/window (keeps payloads small — the
# bulky trades/equity_curve series are intentionally excluded).
_BRIEF_KEYS = (
    "total_return", "sharpe", "sortino", "calmar", "max_drawdown",
    "profit_factor", "win_rate", "n_trades", "net_profit", "total_fees",
    "final_equity",
)


def _strategy_cls(strategy_code: str):
    """Compile a Strategy subclass from source text (AST pre-flight gate runs first)."""
    from ..core.strategy_loader import load_strategy_from_string

    return load_strategy_from_string(strategy_code, validate=True)


def _make_config(config: dict | None):
    """Build a TesterConfig from a JSON dict, rejecting unknown keys with a clear error."""
    from ..tester.config import TesterConfig

    cfg = dict(config or {})
    unknown = set(cfg) - set(_CONFIG_KEYS)
    if unknown:
        raise ValueError(f"unknown config keys {sorted(unknown)}; allowed: {list(_CONFIG_KEYS)}")
    return TesterConfig(**cfg)


def _load_bars(symbol: str, interval: str, start_ms, end_ms, root: str):
    """Load cached bars for (symbol, interval) over the inclusive [start_ms, end_ms] range."""
    from ..data.catalog import Catalog

    bars = Catalog(root).query(symbol, interval, start_ms, end_ms)
    if not bars:
        raise ValueError(
            f"no cached bars for {symbol!r} {interval!r} in range [{start_ms}, {end_ms}]; "
            "run fetch_ohlcv to download it, or call list_cached_data to see what's available"
        )
    return bars


def _grid(strategy_cls, param_grid: dict | None) -> dict:
    """Resolve the optimization grid: explicit override, else the class PARAM_GRID."""
    grid = dict(param_grid or getattr(strategy_cls, "PARAM_GRID", {}) or {})
    if not grid:
        raise ValueError(
            "no param_grid to search: pass param_grid=... or declare PARAM_GRID on the strategy class"
        )
    return grid


def _verdict(v) -> dict | None:
    """Serialize an analysis.overfit.Verdict (or None) to a plain dict."""
    if v is None:
        return None
    return {"level": getattr(v, "level", None), "reasons": list(getattr(v, "reasons", []))}


def _brief(report) -> dict:
    """Headline metrics of a TesterReport as a plain dict (no bulky series)."""
    return {k: getattr(report, k) for k in _BRIEF_KEYS}


def _wf_dict(wf, criterion: str, mode: str) -> dict:
    """Serialize a WalkForwardReport (single-symbol OR portfolio) to a JSON-friendly dict."""
    return {
        "n_windows": wf.n_windows,
        "wf_efficiency": wf.wf_efficiency,
        "wf_consistency": wf.wf_consistency,
        "criterion": criterion,
        "mode": mode,
        "oos": {**_brief(wf.oos_report), "verdict": _verdict(getattr(wf.oos_report, "verdict", None))},
        "windows": [
            {"train_range": list(w.train_range), "test_range": list(w.test_range),
             "best_params": w.best_params, "is_score": w.is_score, "oos_score": w.oos_score,
             "oos_total_return": w.oos_report.total_return,
             "oos_max_drawdown": w.oos_report.max_drawdown}
            for w in wf.windows
        ],
    }


def _portfolio_bars(dataset: str, interval, start_ms, end_ms, root: str):
    """Resolve a DataSet name to (DataSet, interval, {symbol: bars}) from the cache."""
    from ..data.catalog import Catalog
    from ..data.datasets import load_dataset

    ds = load_dataset(dataset, root)
    if ds is None:
        raise ValueError(f"no DataSet named {dataset!r}; call list_data_sets to see available sets")
    iv = interval or ds.interval
    cat = Catalog(root)
    bars_by_symbol = {}
    for sym in ds.symbols:
        bars = cat.query(sym, iv, start_ms, end_ms)
        if bars:
            bars_by_symbol[sym] = bars
    if not bars_by_symbol:
        raise ValueError(
            f"no cached bars for any symbol in DataSet {dataset!r} at interval {iv!r}; fetch the data first"
        )
    return ds, iv, bars_by_symbol


# --- discovery -------------------------------------------------------------

def validate_strategy(strategy_code: str) -> dict:
    """Static pre-flight check of strategy source (no execution); returns {ok, problems}.

    Use this in a write -> validate -> fix loop before run_backtest: ``problems`` is the exact set of
    AST-gate reasons load_strategy_from_string would reject, so the agent can self-correct its code.
    """
    from ..core.sandbox.preflight import check_strategy_source

    problems = check_strategy_source(strategy_code)
    return {"ok": not problems, "problems": list(problems)}


def list_cached_data(root: str = _DATA_ROOT) -> dict:
    """List every cached (symbol, interval) dataset with bar count + time range (for discovery)."""
    from ..data.catalog import Catalog

    ds = Catalog(root).list_datasets()
    return {
        "n": len(ds),
        "datasets": [
            {"symbol": d.symbol, "interval": d.interval, "n_bars": d.n_bars,
             "start_ts": d.start_ts, "end_ts": d.end_ts}
            for d in ds
        ],
    }


def list_data_sets(root: str = _DATA_ROOT) -> dict:
    """List named DataSets (symbol universes) for portfolio backtests: name, interval, symbols."""
    from ..data.datasets import list_datasets, load_dataset

    out = []
    for nm in list_datasets(root):
        ds = load_dataset(nm, root)
        if ds is not None:
            out.append({"name": ds.name, "interval": ds.interval, "n_symbols": len(ds.symbols),
                        "symbols": ds.symbols, "dynamic": ds.is_dynamic(), "benchmark": ds.benchmark})
    return {"n": len(out), "datasets": out}


def list_strategy_templates() -> dict:
    """Return the gallery of ready-to-run example strategies (name, category, blurb, full code)."""
    from ..analysis.strategy_templates import TEMPLATES

    return {
        "n": len(TEMPLATES),
        "templates": [
            {"name": t.name, "category": t.category, "description": t.description, "code": t.code}
            for t in TEMPLATES
        ],
    }


def list_scanner_rules() -> dict:
    """List available screener rules (base + saved composites) for run_scanner."""
    from ..analysis.screener import RULES, CompositeStore, composites

    try:
        CompositeStore().load()  # register any saved composites into the live registry
    except Exception:
        pass
    base = [{"name": r.name, "description": r.description, "type": "base"} for r in RULES]
    comp = [{"name": c.name, "description": c.description, "type": "composite"} for c in composites()]
    return {"n": len(base) + len(comp), "rules": base + comp}


# --- single-symbol -------------------------------------------------------

def run_backtest(strategy_code: str, symbol: str, interval: str, start_ms=None, end_ms=None, *,
                 params: dict | None = None, config: dict | None = None, root: str = _DATA_ROOT) -> dict:
    """Compile ``strategy_code`` and run ONE backtest on cached ``symbol``/``interval`` bars.

    Returns the standardized TesterReport headline metrics (Sharpe/Sortino/Calmar/drawdown/profit
    factor/win-rate/...) plus symbol/interval/n_bars. ``params`` overrides strategy attributes;
    ``config`` sets costs/capital (cash, fee_rate, slippage, ...). For daily bars pass
    ``config={"periods_per_year": 252}`` so the annualized ratios are correct.
    """
    cls = _strategy_cls(strategy_code)
    bars = _load_bars(symbol, interval, start_ms, end_ms, root)
    strat = cls.make(**params) if params else cls()
    from ..tester.strategy_tester import StrategyTester

    report = StrategyTester(strat, bars, _make_config(config)).run()
    out = report.as_dict()
    out.update(symbol=symbol, interval=interval, n_bars=len(bars))
    return out


def run_optimization(strategy_code: str, symbol: str, interval: str, start_ms=None, end_ms=None, *,
                     param_grid: dict | None = None, criterion: str = "sharpe", method: str = "grid",
                     top_n: int = 10, seed: int = 0, n_trials: int | None = None, sampler: str = "tpe",
                     config: dict | None = None, root: str = _DATA_ROOT) -> dict:
    """Optimize ``strategy_code`` over a parameter grid, ranking combos by ``criterion``.

    ``param_grid`` defaults to the class ``PARAM_GRID``. ``method`` is grid | random | genetic |
    bayesian (bayesian needs the optional optuna extra; ``sampler`` = tpe | gpsampler | cmaes).
    Returns the best combo (params + score + headline metrics), the top-N ranked combos, and the
    correlation-aware effective trial count for overfit-awareness.
    """
    cls = _strategy_cls(strategy_code)
    grid = _grid(cls, param_grid)
    bars = _load_bars(symbol, interval, start_ms, end_ms, root)
    from ..tester.strategy_tester import StrategyTester

    rep = StrategyTester(cls(), bars, _make_config(config)).optimize(
        cls.make, grid, criterion=criterion, method=method, seed=seed,
        n_trials=n_trials, sampler=sampler,
    )
    return {
        "criterion": rep.criterion,
        "n_trials": rep.n_trials,
        "effective_n": rep.effective_n,
        "best": {"params": rep.best.params, "score": rep.best.score, "metrics": _brief(rep.best.report)},
        "ranked": [{"params": t.params, "score": t.score} for t in rep.ranked[:top_n]],
    }


def run_walk_forward(strategy_code: str, symbol: str, interval: str, start_ms=None, end_ms=None, *,
                     param_grid: dict | None = None, n_splits: int = 4, criterion: str = "sharpe",
                     mode: str = "anchored", method: str = "grid", seed: int = 0,
                     n_trials: int | None = None, sampler: str = "tpe",
                     config: dict | None = None, root: str = _DATA_ROOT) -> dict:
    """Walk-forward validation: optimize-on-train -> measure-OOS-on-test across ``n_splits`` windows.

    This is the robust "split into time chunks and validate each out-of-sample" check. ``mode`` is
    anchored (expanding train) or rolling (fixed-width sliding). Returns wf_efficiency (how much
    in-sample edge survives OOS), wf_consistency, per-window IS-vs-OOS scores, and the stitched OOS
    report with an overfitting ``verdict`` (level + plain-language reasons).
    """
    cls = _strategy_cls(strategy_code)
    grid = _grid(cls, param_grid)
    bars = _load_bars(symbol, interval, start_ms, end_ms, root)
    from ..tester.strategy_tester import StrategyTester

    wf = StrategyTester(cls(), bars, _make_config(config)).walk_forward(
        cls.make, grid, n_splits=n_splits, criterion=criterion, mode=mode,
        method=method, seed=seed, n_trials=n_trials, sampler=sampler,
    )
    return _wf_dict(wf, criterion, mode)


# --- portfolio (multi-symbol, shared cash over a DataSet) ------------------

def run_portfolio_backtest(strategy_code: str, dataset: str, interval: str | None = None,
                           start_ms=None, end_ms=None, *, params: dict | None = None,
                           max_open_positions: int = 0, config: dict | None = None,
                           root: str = _DATA_ROOT) -> dict:
    """Run ONE portfolio backtest of ``strategy_code`` across the symbols of ``dataset`` (shared cash).

    One copy of the strategy runs per symbol with shared cash and an optional ``max_open_positions``
    cap; dynamic-membership ranges from the DataSet are honored. Returns portfolio headline metrics,
    per-symbol PnL, the benchmark label, and the symbol list. ``interval`` defaults to the DataSet's.
    """
    cls = _strategy_cls(strategy_code)
    ds, iv, bars_by_symbol = _portfolio_bars(dataset, interval, start_ms, end_ms, root)
    from ..tester.portfolio_tester import PortfolioStrategyTester

    pt = PortfolioStrategyTester(bars_by_symbol, _make_config(config),
                                 max_open_positions=max_open_positions, ranges=ds.ranges or None)
    factory = (lambda: cls.make(**params)) if params else cls
    report = pt.run(factory)
    out = _brief(report)
    out.update(dataset=ds.name, interval=iv, n_symbols=len(bars_by_symbol),
               symbols=list(bars_by_symbol), max_open_positions=max_open_positions,
               per_symbol_pnl=report.per_symbol_pnl, benchmark_label=report.benchmark_label,
               verdict=_verdict(report.verdict))
    return out


def run_portfolio_walk_forward(strategy_code: str, dataset: str, interval: str | None = None,
                               start_ms=None, end_ms=None, *, param_grid: dict | None = None,
                               n_splits: int = 4, criterion: str = "sharpe", mode: str = "anchored",
                               method: str = "grid", seed: int = 0, n_trials: int | None = None,
                               sampler: str = "tpe", max_open_positions: int = 0,
                               config: dict | None = None, root: str = _DATA_ROOT) -> dict:
    """Date-based walk-forward of ``strategy_code`` across a DataSet, scored on PORTFOLIO equity.

    Each window optimizes the param grid on the train slice then measures the best combo OOS on the
    test slice (every symbol's bars sliced to the shared time window). Returns wf_efficiency,
    per-window IS-vs-OOS scores, and the stitched OOS report with an overfitting verdict.
    """
    cls = _strategy_cls(strategy_code)
    grid = _grid(cls, param_grid)
    ds, iv, bars_by_symbol = _portfolio_bars(dataset, interval, start_ms, end_ms, root)
    from ..tester.portfolio_tester import PortfolioStrategyTester

    wf = PortfolioStrategyTester(bars_by_symbol, _make_config(config),
                                 max_open_positions=max_open_positions, ranges=ds.ranges or None).walk_forward(
        cls.make, grid, n_splits=n_splits, criterion=criterion, mode=mode,
        method=method, seed=seed, n_trials=n_trials, sampler=sampler,
    )
    out = _wf_dict(wf, criterion, mode)
    out.update(dataset=ds.name, interval=iv, n_symbols=len(bars_by_symbol))
    return out


# --- scanner (screen a symbol universe by an indicator rule) ---------------

def run_scanner(rule_name: str, interval: str, *, symbols: list | None = None,
                start_ms=None, end_ms=None, min_volume: float = 0.0, root: str = _DATA_ROOT) -> dict:
    """Screen a universe by a named rule; return symbols ranked long-first by setup strength.

    ``rule_name`` is a base rule (see list_scanner_rules) or a saved composite. ``symbols`` defaults
    to every cached symbol that has ``interval``. Each row is {symbol, signal, value, last}. Pass
    ``min_volume`` to drop thin names (when volume is cached).
    """
    from ..analysis.screener import RULES, composites, rule_by_name, screen
    from ..data.catalog import Catalog

    from ..analysis.screener import CompositeStore
    try:
        CompositeStore().load()  # so saved composites resolve by name
    except Exception:
        pass
    rule = rule_by_name(rule_name)
    if rule is None:
        avail = [r.name for r in RULES] + [c.name for c in composites()]
        raise ValueError(f"unknown rule {rule_name!r}; available: {avail}")

    cat = Catalog(root)
    syms = list(symbols) if symbols else [s for s in cat.symbols() if interval in cat.intervals(s)]
    symbol_closes: dict = {}
    symbol_volumes: dict = {}
    for sym in syms:
        bars = cat.query(sym, interval, start_ms, end_ms)
        if not bars:
            continue
        symbol_closes[sym] = [b.close for b in bars]
        if min_volume > 0.0:
            symbol_volumes[sym] = [getattr(b, "volume", 0.0) or 0.0 for b in bars]

    rows = screen(symbol_closes, rule, symbol_volumes=(symbol_volumes or None), min_volume=min_volume)
    return {
        "rule": rule_name, "interval": interval, "n_scanned": len(symbol_closes), "n": len(rows),
        "rows": [{"symbol": r.symbol, "signal": r.signal, "value": r.value, "last": r.last} for r in rows],
    }
