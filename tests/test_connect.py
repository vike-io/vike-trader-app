"""Tests for ai.connect — generating + installing the vike-trader MCP config for local Claude."""

import json
from pathlib import Path

from vike_trader_app.ai import connect


def test_mcp_server_entry_shape(tmp_path):
    e = connect.mcp_server_entry(str(tmp_path))
    assert Path(e["command"]).name.lower().startswith("python")
    assert e["args"] == ["-m", "vike_trader_app.ai.mcp_server"]
    assert e["env"]["VIKE_DATA_ROOT"] == str(tmp_path.resolve())
    assert "VIKE_TELEMETRY" not in e["env"]


def test_mcp_server_entry_telemetry(tmp_path):
    e = connect.mcp_server_entry(str(tmp_path), telemetry=True, telemetry_url="https://x/y")
    assert e["env"]["VIKE_TELEMETRY"] == "1"
    assert e["env"]["VIKE_TELEMETRY_URL"] == "https://x/y"


def test_install_preserves_existing_config(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}), encoding="utf-8")
    connect.install_into_claude_desktop(str(tmp_path / "data"), path=cfg)
    out = json.loads(cfg.read_text(encoding="utf-8"))
    assert out["theme"] == "dark"                          # unrelated keys preserved
    assert out["mcpServers"]["other"] == {"command": "x"}  # other servers preserved
    assert out["mcpServers"]["vike-trader"]["args"] == ["-m", "vike_trader_app.ai.mcp_server"]


def test_install_creates_when_missing(tmp_path):
    cfg = tmp_path / "sub" / "claude_desktop_config.json"
    connect.install_into_claude_desktop(str(tmp_path / "data"), path=cfg)
    assert cfg.exists()
    assert "vike-trader" in json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]


def test_claude_code_command_str(tmp_path):
    s = connect.claude_code_command_str(str(tmp_path))
    assert "claude mcp add vike-trader" in s
    assert "--env" in s and "VIKE_DATA_ROOT=" in s
    assert " -- " in s
    assert "vike_trader_app.ai.mcp_server" in s


def test_telemetry_url_env_override(monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY_URL", "https://t.example/u")
    assert connect.telemetry_url() == "https://t.example/u"


def test_telemetry_url_defaults_to_constant(monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY_URL", raising=False)
    assert connect.telemetry_url() == connect.DEFAULT_TELEMETRY_URL


def test_install_writes_telemetry_env_when_enabled(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    connect.install_into_claude_desktop(
        str(tmp_path / "data"), path=cfg, telemetry=True, telemetry_url="https://t.example/u"
    )
    env = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["vike-trader"]["env"]
    assert env["VIKE_TELEMETRY"] == "1"
    assert env["VIKE_TELEMETRY_URL"] == "https://t.example/u"


def test_install_omits_telemetry_env_by_default(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    connect.install_into_claude_desktop(str(tmp_path / "data"), path=cfg)
    env = json.loads(cfg.read_text(encoding="utf-8"))["mcpServers"]["vike-trader"]["env"]
    assert "VIKE_TELEMETRY" not in env


def test_server_env_injects_default_token_when_telemetry_on(tmp_path, monkeypatch):
    monkeypatch.delenv("VIKE_TELEMETRY_TOKEN", raising=False)
    e = connect.mcp_server_entry(str(tmp_path), telemetry=True, telemetry_url="https://t.example/u")
    assert connect.DEFAULT_TELEMETRY_TOKEN  # a baked default exists so the Connect button is universal
    assert e["env"]["VIKE_TELEMETRY_TOKEN"] == connect.DEFAULT_TELEMETRY_TOKEN


def test_server_env_token_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY_TOKEN", "override-123")
    e = connect.mcp_server_entry(str(tmp_path), telemetry=True, telemetry_url="https://t.example/u")
    assert e["env"]["VIKE_TELEMETRY_TOKEN"] == "override-123"


def test_server_env_no_token_when_telemetry_off(tmp_path, monkeypatch):
    monkeypatch.setenv("VIKE_TELEMETRY_TOKEN", "x")
    e = connect.mcp_server_entry(str(tmp_path))  # telemetry disabled -> nothing injected
    assert "VIKE_TELEMETRY_TOKEN" not in e["env"]
