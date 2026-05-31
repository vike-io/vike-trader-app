"""MCP server exposing the vike engine as tools over stdio (FastMCP).

Launch with ``vike-mcp`` (console script) or ``python -m vike_trader_app.ai.mcp_server``. Each
tool is a thin wrapper over ``ai/services.py`` (the single source of truth). Requires the optional
extra: ``pip install vike_trader_app[mcp]``.
"""

from . import services


def run_sma_backtest(closes, fast: int, slow: int, fee_rate: float = 0.0) -> dict:
    """Backtest an SMA(fast)x SMA(slow) crossover on a list of close prices; return metrics."""
    return services.run_sma_backtest(closes, fast=fast, slow=slow, fee_rate=fee_rate)


def optimize_sma(closes, fasts: list[int], slows: list[int], fee_rate: float = 0.0,
                 top_n: int = 10) -> dict:
    """Sweep SMA crossover parameters over fasts x slows; return the top-N ranked combos."""
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


_TOOLS = [run_sma_backtest, optimize_sma, fetch_ohlcv, overfit_check, query_kb,
          list_indicators, compute_indicator]


def build_server():
    """Construct the FastMCP server with all tools registered."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError("MCP server requires the optional extra: pip install vike_trader_app[mcp]") from e
    server = FastMCP("vike-trader")
    for fn in _TOOLS:
        server.tool()(fn)
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


def main() -> None:
    """Console-script entry point: run the stdio MCP server."""
    build_server().run()


if __name__ == "__main__":
    main()
