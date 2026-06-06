"""Tests for ai.telemetry — opt-in MCP usage telemetry (privacy + schema preservation)."""

import json

import pytest

from vike_trader_app.ai import mcp_server, telemetry


def test_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY", raising=False)
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))
    assert telemetry.instrument(lambda x: x * 2)(3) == 6
    assert not (tmp_path / "mcp-usage.jsonl").exists()


def test_records_safe_event_never_leaks_source(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY", "1")
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))

    @telemetry.instrument
    def fake(strategy_code: str, symbol: str, config: dict | None = None):
        return {"ran": True}

    fake(strategy_code="class StrategyABC: pass", symbol="AAPL", config={"cash": 5000})
    ev = json.loads((tmp_path / "mcp-usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    assert ev["tool"] == "fake" and ev["ok"] is True and "duration_ms" in ev
    assert ev["args"]["symbol"] == "AAPL"
    # source replaced by a sha + length; config reduced to its keys
    assert "strategy_code_sha" in ev["args"] and ev["args"]["strategy_code_len"] == 23
    assert ev["args"]["config"] == {"_keys": ["cash"]}
    assert "StrategyABC" not in json.dumps(ev)


def test_records_error_and_reraises(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY", "1")
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))

    @telemetry.instrument
    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        boom()
    ev = json.loads((tmp_path / "mcp-usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert ev["ok"] is False and ev["error"] == "ValueError"


def test_build_server_preserves_tool_schema_through_wrapper():
    srv = mcp_server.build_server()
    names = mcp_server.tool_names(srv)
    assert {"run_backtest", "run_scanner", "run_portfolio_backtest"} <= set(names)
    tools = {t.name: t for t in srv._tool_manager.list_tools()}
    props = tools["run_backtest"].parameters["properties"]
    assert {"strategy_code", "symbol", "interval"} <= set(props)
