"""MCP server exposing the vike engine as tools over stdio (FastMCP).

Launch with ``vike-mcp`` (console script) or ``python -m vike_trader_app.ai.mcp_server``. Each
tool is a thin wrapper over ``ai/services.py`` (the single source of truth). Requires the optional
extra: ``pip install vike_trader_app[mcp]``.
"""

from . import services, telemetry


def run_sma_backtest(closes: list[float], fast: int, slow: int, fee_rate: float = 0.0) -> dict:
    """Backtest an SMA(fast)x SMA(slow) crossover on a list of close prices; return metrics.

    ``closes`` is a JSON array of numbers, e.g. [100.0, 101.2, 100.8, ...].
    """
    return services.run_sma_backtest(closes, fast=fast, slow=slow, fee_rate=fee_rate)


def optimize_sma(closes: list[float], fasts: list[int], slows: list[int], fee_rate: float = 0.0,
                 top_n: int = 10) -> dict:
    """Sweep SMA crossover parameters over fasts x slows; return the top-N ranked combos.

    ``closes`` is a JSON array of numbers, e.g. [100.0, 101.2, 100.8, ...].
    """
    return services.optimize_sma(closes, fasts, slows, fee_rate=fee_rate, top_n=top_n)


def fetch_ohlcv(symbol: str, interval: str, start_ms: int, end_ms: int,
                source: str = "binance") -> dict:
    """Fetch + cache OHLCV for a symbol; return a summary incl. the closes for backtesting."""
    return services.fetch_ohlcv(symbol, interval, start_ms, end_ms, source=source)


def overfit_check(observed_sr: float, trial_sharpes: list[float], n_obs: int,
                  n_splits: int = 4) -> dict:
    """Deflated Sharpe + verdict for an observed Sharpe given all trial Sharpes."""
    return services.overfit_check(observed_sr, trial_sharpes, n_obs, n_splits=n_splits)


def query_kb(query: str, k: int = 5) -> dict:
    """Search the vike-trader codebase/knowledge base; return the top-k passages."""
    return services.query_kb(query, k=k)


def list_indicators(category: str | None = None) -> dict:
    """List available technical indicators and their parameter metadata."""
    return services.list_indicators(category)


def compute_indicator(name: str, ohlcv: dict, params: dict | None = None) -> dict:
    """Compute a named technical indicator over an OHLCV column dict; return its output series."""
    return services.compute_indicator(name, ohlcv, params)


# --- discovery -------------------------------------------------------------

def list_cached_data(root: str = "storage/parquet") -> dict:
    """List cached (symbol, interval) datasets with bar counts + time ranges (data discovery)."""
    return services.list_cached_data(root=root)


def list_data_sets(root: str = "storage/parquet") -> dict:
    """List named DataSets (symbol universes) for portfolio backtests: name, interval, symbols."""
    return services.list_data_sets(root=root)


def list_strategy_templates() -> dict:
    """List ready-to-run example strategies (name, category, blurb, full code) to author from."""
    return services.list_strategy_templates()


def list_scanner_rules() -> dict:
    """List available screener rules (base + saved composites) usable by run_scanner."""
    return services.list_scanner_rules()


def validate_strategy(strategy_code: str) -> dict:
    """Static pre-flight check of strategy source (no execution); returns {ok, problems} for self-repair."""
    return services.validate_strategy(strategy_code)


# --- single-symbol ---------------------------------------------------------

def run_backtest(strategy_code: str, symbol: str, interval: str,
                 start_ms: int | None = None, end_ms: int | None = None,
                 params: dict | None = None, config: dict | None = None) -> dict:
    """Compile a strategy and run ONE backtest on cached bars; return standardized headline metrics."""
    return services.run_backtest(strategy_code, symbol, interval, start_ms, end_ms,
                                 params=params, config=config)


def run_optimization(strategy_code: str, symbol: str, interval: str,
                     start_ms: int | None = None, end_ms: int | None = None,
                     param_grid: dict | None = None, criterion: str = "sharpe",
                     method: str = "grid", top_n: int = 10, seed: int = 0,
                     n_trials: int | None = None, sampler: str = "tpe",
                     config: dict | None = None) -> dict:
    """Optimize a strategy over a param grid (grid/random/genetic/bayesian); return ranked combos."""
    return services.run_optimization(strategy_code, symbol, interval, start_ms, end_ms,
                                     param_grid=param_grid, criterion=criterion, method=method,
                                     top_n=top_n, seed=seed, n_trials=n_trials, sampler=sampler,
                                     config=config)


def run_walk_forward(strategy_code: str, symbol: str, interval: str,
                     start_ms: int | None = None, end_ms: int | None = None,
                     param_grid: dict | None = None, n_splits: int = 4,
                     criterion: str = "sharpe", mode: str = "anchored", method: str = "grid",
                     seed: int = 0, n_trials: int | None = None, sampler: str = "tpe",
                     config: dict | None = None) -> dict:
    """Walk-forward validation across time chunks; return wf_efficiency, per-window OOS, overfit verdict."""
    return services.run_walk_forward(strategy_code, symbol, interval, start_ms, end_ms,
                                     param_grid=param_grid, n_splits=n_splits, criterion=criterion,
                                     mode=mode, method=method, seed=seed, n_trials=n_trials,
                                     sampler=sampler, config=config)


# --- portfolio (multi-symbol over a DataSet) -------------------------------

def run_portfolio_backtest(strategy_code: str, dataset: str, interval: str | None = None,
                           start_ms: int | None = None, end_ms: int | None = None,
                           params: dict | None = None, max_open_positions: int = 0,
                           config: dict | None = None) -> dict:
    """Run ONE portfolio backtest across a DataSet's symbols (shared cash); return portfolio metrics."""
    return services.run_portfolio_backtest(strategy_code, dataset, interval, start_ms, end_ms,
                                           params=params, max_open_positions=max_open_positions,
                                           config=config)


def run_portfolio_walk_forward(strategy_code: str, dataset: str, interval: str | None = None,
                               start_ms: int | None = None, end_ms: int | None = None,
                               param_grid: dict | None = None, n_splits: int = 4,
                               criterion: str = "sharpe", mode: str = "anchored", method: str = "grid",
                               seed: int = 0, n_trials: int | None = None, sampler: str = "tpe",
                               max_open_positions: int = 0, config: dict | None = None) -> dict:
    """Date-based walk-forward across a DataSet, scored on portfolio equity; return per-window OOS + verdict."""
    return services.run_portfolio_walk_forward(strategy_code, dataset, interval, start_ms, end_ms,
                                               param_grid=param_grid, n_splits=n_splits, criterion=criterion,
                                               mode=mode, method=method, seed=seed, n_trials=n_trials,
                                               sampler=sampler, max_open_positions=max_open_positions,
                                               config=config)


# --- scanner ---------------------------------------------------------------

def run_scanner(rule_name: str, interval: str, symbols: list | None = None,
                start_ms: int | None = None, end_ms: int | None = None,
                min_volume: float = 0.0) -> dict:
    """Screen a symbol universe by a named rule; return rows ranked long-first by setup strength."""
    return services.run_scanner(rule_name, interval, symbols=symbols, start_ms=start_ms,
                                end_ms=end_ms, min_volume=min_volume)


_TOOLS = [run_sma_backtest, optimize_sma, fetch_ohlcv, overfit_check, query_kb,
          list_indicators, compute_indicator,
          list_cached_data, list_data_sets, list_strategy_templates, list_scanner_rules,
          validate_strategy, run_backtest, run_optimization, run_walk_forward,
          run_portfolio_backtest, run_portfolio_walk_forward, run_scanner]


def build_server():
    """Construct the FastMCP server with all tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError("MCP server requires the optional extra: pip install vike_trader_app[mcp]") from e
    server = FastMCP("vike-trader")
    for fn in _TOOLS:
        # instrument() records opt-in usage telemetry and preserves the schema (no-op when off)
        server.tool()(telemetry.instrument(fn))
    return server


def tool_names(server) -> list[str]:
    """List of registered tool names read from the real FastMCP registry.

    In mcp 1.27.2, ``_tool_manager.list_tools()`` is synchronous and returns a list of
    ``Tool`` objects each with a ``.name`` attribute.  Falls back to function ``__name__``
    only when the manager attribute is absent (future API change).
    """
    mgr = getattr(server, "_tool_manager", None)
    if mgr is not None and hasattr(mgr, "list_tools"):
        return [t.name for t in mgr.list_tools()]
    # pragma: no cover — fallback for unexpected FastMCP API changes
    return [fn.__name__ for fn in _TOOLS]


def _warm_jit() -> None:
    """Initialise Numba's backtest kernels ON THE MAIN THREAD before the server serves requests.

    Two problems this prevents, both of which made the first MCP backtest "never come back":
      * Cold JIT compile: the first backtest in a fresh process otherwise compiles the @njit
        kernels inline (tens of seconds), blowing past the MCP client's tool timeout. (cache=True
        persists this across runs, so it's a one-time-per-install cost.)
      * Numba's first per-process dispatch is pathologically slow OFF the main thread — and FastMCP
        runs sync tools in a worker thread, so the first tool call paid 30s+ EVEN with a warm cache,
        while the SECOND was 0.5s. Forcing that first dispatch here (main thread) makes every
        worker-thread tool call fast.

    A 5-bar series compiles the identical float64 type-signatures as a 5000-bar one. Best-effort."""
    try:
        services.run_sma_backtest([1.0, 2.0, 3.0, 4.0, 5.0], fast=2, slow=3)
        services.optimize_sma([1.0, 2.0, 3.0, 4.0, 5.0], [2], [3])
    except Exception:  # noqa: BLE001 - warm-up is purely an optimization; never block startup
        pass


def main() -> None:
    """Console-script entry point: run the stdio MCP server."""
    import os

    server = build_server()
    if os.environ.get("VIKE_MCP_WARMUP", "1") != "0":
        _warm_jit()   # synchronous, MAIN thread — must precede run() (see _warm_jit docstring)
    server.run()


if __name__ == "__main__":
    main()
