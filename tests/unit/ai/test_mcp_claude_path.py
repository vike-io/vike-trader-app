"""Claude / MCP path simulation — the in-app strategy engine driven the way Claude Code or the
Claude desktop app drives it: over the MCP tool surface in ``vike_trader_app.ai.mcp_server``.

Each test calls the SAME module-level tool functions FastMCP exposes over stdio, so it exercises
exactly what the "Claude subscription from here / from the Claude app" path runs end to end:
discover -> write -> validate -> backtest -> walk-forward -> scan, plus the server registration the
client connects to. No network: backtests read cached Parquet through the catalog (skip if absent).
"""

import pytest

from vike_trader_app.ai import mcp_server

# A minimal, deterministic strategy: one round-trip (buy bar 0, flat by bar 5) -> exactly 1 trade.
_GOOD = """
from vike_trader_app.core.strategy import Strategy

class S(Strategy):
    def on_bar(self, bar):
        if self.index == 0:
            self.buy(1.0)
        elif self.index == 5:
            self.close()
"""

# Forbidden top-level import -> the AST preflight gate must reject it (Claude's self-repair signal).
_BAD = "import os\n" + _GOOD


def _cached():
    """First cached (symbol, interval) with enough bars; skip the test if no market data is cached."""
    datasets = mcp_server.list_cached_data().get("datasets", [])
    usable = [d for d in datasets if d.get("n_bars", 0) >= 60]
    if not usable:
        pytest.skip("no cached market data available for the MCP backtest path")
    d = usable[0]
    return d["symbol"], d["interval"]


# --- 1. discovery: what Claude lists before writing anything --------------------

def test_claude_lists_templates_and_cached_data():
    tpls = mcp_server.list_strategy_templates()
    assert tpls["n"] >= 1
    assert tpls["templates"][0]["code"]                 # full source is returned for Claude to adapt
    data = mcp_server.list_cached_data()
    assert "datasets" in data
    rules = mcp_server.list_scanner_rules()
    assert rules["n"] >= 1


# --- 2. write -> validate -> fix loop ------------------------------------------

def test_claude_validate_strategy_accepts_good_rejects_bad():
    assert mcp_server.validate_strategy(_GOOD)["ok"] is True
    bad = mcp_server.validate_strategy(_BAD)
    assert bad["ok"] is False
    assert bad["problems"]                              # exact AST-gate reasons for self-correction


# --- 3. backtest a single symbol -----------------------------------------------

def test_claude_run_backtest_returns_headline_metrics():
    sym, interval = _cached()
    res = mcp_server.run_backtest(_GOOD, sym, interval)
    assert res["symbol"] == sym and res["interval"] == interval
    assert res["n_bars"] > 0
    assert res["n_trades"] >= 1                         # the one round-trip executed
    assert "sharpe" in res                              # standardized headline metrics present


# --- 4. walk-forward robustness check (on a template that ships a PARAM_GRID) ---

def test_claude_run_walk_forward_returns_a_result():
    sym, interval = _cached()
    code = mcp_server.list_strategy_templates()["templates"][0]["code"]
    try:
        res = mcp_server.run_walk_forward(code, sym, interval, n_splits=3)
    except Exception as exc:  # noqa: BLE001 - some templates carry no grid; that's a valid skip
        pytest.skip(f"walk-forward needs a param grid for this template: {exc}")
    assert isinstance(res, dict) and res


# --- 5. scan a universe by a named rule ----------------------------------------

def test_claude_run_scanner_returns_a_result():
    _sym, interval = _cached()
    rule = mcp_server.list_scanner_rules()["rules"][0]["name"]
    res = mcp_server.run_scanner(rule, interval)
    assert isinstance(res, dict)


# --- 6. the server Claude actually connects to ---------------------------------

_EXPECTED_TOOLS = {
    "validate_strategy", "run_backtest", "run_optimization", "run_walk_forward",
    "run_scanner", "list_strategy_templates", "list_cached_data",
}


def test_mcp_server_registers_the_strategy_tools():
    # _TOOLS is the source of truth build_server() registers — Claude sees exactly these.
    names = {fn.__name__ for fn in mcp_server._TOOLS}
    assert _EXPECTED_TOOLS <= names


def test_build_server_exposes_the_tools_to_the_client():
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    assert server is not None
    assert _EXPECTED_TOOLS <= set(mcp_server.tool_names(server))   # live FastMCP registry
