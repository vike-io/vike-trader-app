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


def _capture_post_headers(monkeypatch):
    """Run telemetry._post with the network stubbed out; return the headers it built."""
    import urllib.request

    captured = {}

    class _FakeReq:
        def __init__(self, url, data=None, headers=None, method=None):
            captured["headers"] = dict(headers or {})

    class _FakeResp:
        def close(self):
            pass

    monkeypatch.setattr(urllib.request, "Request", _FakeReq)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _FakeResp())
    telemetry._post({"ts_ms": 1, "client": "t", "tool": "x"}, "http://example.invalid/telemetry")
    return captured.get("headers", {})


def test_post_attaches_token_header_when_set(monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY_TOKEN", "s3cr3t")
    headers = _capture_post_headers(monkeypatch)
    assert headers.get("x-vike-token") == "s3cr3t"
    assert headers.get("Content-Type") == "application/json"


def test_post_omits_token_header_when_unset(monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY_TOKEN", raising=False)
    headers = _capture_post_headers(monkeypatch)
    assert "x-vike-token" not in headers


def test_report_crash_noop_when_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY", raising=False)
    monkeypatch.delenv("VIKE_CRASH_REPORTS", raising=False)
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(telemetry, "record", lambda ev: calls.append(ev))

    telemetry.report_crash({"kind": "python_main", "exc_type": "E", "traceback": "t"})

    assert calls == []  # nothing recorded or uploaded when opt-in is off


def test_report_crash_records_scrubbed_event_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY", "1")
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(telemetry, "record", lambda ev: calls.append(ev))

    telemetry.report_crash({"kind": "native", "exc_type": "Segfault", "traceback": "boom"})

    assert len(calls) == 1
    ev = calls[0]
    assert ev["type"] == "crash" and ev["kind"] == "native"
    assert "client" in ev and "ts_ms" in ev
    assert "python" in ev["env"]  # safe environment block present


def test_report_crash_enables_independently_via_crash_reports_env(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY", raising=False)  # usage telemetry OFF
    monkeypatch.setenv("VIKE_CRASH_REPORTS", "1")  # crash reporting ON on its own
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(telemetry, "record", lambda ev: calls.append(ev))

    telemetry.report_crash({"kind": "python_main"})

    assert len(calls) == 1


def test_report_crash_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_CRASH_REPORTS", "1")
    monkeypatch.setenv("VIKE_TELEMETRY_DIR", str(tmp_path))

    def boom(ev):
        raise RuntimeError("network down")

    monkeypatch.setattr(telemetry, "record", boom)
    telemetry.report_crash({"kind": "python_main"})  # must not propagate


def test_build_server_preserves_tool_schema_through_wrapper():
    srv = mcp_server.build_server()
    names = mcp_server.tool_names(srv)
    assert {"run_backtest", "run_scanner", "run_portfolio_backtest"} <= set(names)
    tools = {t.name: t for t in srv._tool_manager.list_tools()}
    props = tools["run_backtest"].parameters["properties"]
    assert {"strategy_code", "symbol", "interval"} <= set(props)
