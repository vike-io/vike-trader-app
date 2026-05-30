"""MCP server: thin FastMCP layer over ai.services. Skips when the [mcp] extra is absent."""

import pytest

pytest.importorskip("mcp")


def test_server_builds_and_registers_tools():
    from vike_trader_app.ai import mcp_server

    server = mcp_server.build_server()
    names = mcp_server.tool_names(server)
    assert {"run_sma_backtest", "optimize_sma", "fetch_ohlcv", "overfit_check"} <= set(names)


def test_tool_wrappers_delegate_to_services():
    from vike_trader_app.ai import mcp_server

    closes = [100.0 + (i % 7) for i in range(60)]
    out = mcp_server.run_sma_backtest(closes, fast=5, slow=20, fee_rate=0.0)
    assert out["params"] == {"fast": 5, "slow": 20}
    assert "sharpe" in out
