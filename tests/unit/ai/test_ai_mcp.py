"""MCP server: thin FastMCP layer over ai.services. Skips when the [mcp] extra is absent."""

import pytest

pytest.importorskip("mcp")


def test_server_builds_and_registers_tools():
    from vike_trader_app.ai import mcp_server

    server = mcp_server.build_server()
    names = mcp_server.tool_names(server)
    assert {"run_sma_backtest", "optimize_sma", "fetch_ohlcv", "overfit_check", "query_kb",
            "list_indicators", "compute_indicator"} <= set(names)


def test_tool_wrappers_delegate_to_services():
    from vike_trader_app.ai import mcp_server

    closes = [100.0 + (i % 7) for i in range(60)]
    out = mcp_server.run_sma_backtest(closes, fast=5, slow=20, fee_rate=0.0)
    assert out["params"] == {"fast": 5, "slow": 20}
    assert "sharpe" in out


def test_closes_schema_is_a_number_array():
    """`closes` MUST advertise an array-of-numbers schema. When it was unannotated, FastMCP emitted
    no array type and LLM clients passed `closes` as a STRING — the reported MCP parse/hang bug."""
    from vike_trader_app.ai import mcp_server

    server = mcp_server.build_server()
    tools = {t.name: t for t in server._tool_manager.list_tools()}
    for name in ("run_sma_backtest", "optimize_sma"):
        schema = getattr(tools[name], "parameters", None) or tools[name].input_schema
        closes = schema["properties"]["closes"]
        assert closes.get("type") == "array", f"{name}.closes is not an array: {closes}"
        assert closes["items"].get("type") == "number"


def test_warm_jit_runs_clean():
    """The startup JIT warm-up (forces Numba init on the main thread) must run without raising."""
    from vike_trader_app.ai import mcp_server

    mcp_server._warm_jit()  # smoke: no exception, returns None

