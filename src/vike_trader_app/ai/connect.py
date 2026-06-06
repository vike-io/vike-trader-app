"""Wire the user's LOCAL Claude client to the vike-trader MCP server.

The point: a Claude Pro/Max user drives the engine over MCP on their OWN
subscription (zero inference cost to us). This generates the MCP server entry for
THIS install — the interpreter, the module, an ABSOLUTE data root, and optional
telemetry env — and installs it into Claude Desktop's config and/or emits the
Claude Code CLI command. Pure (no Qt) so it's unit-testable; the GUI button is a
thin caller.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SERVER_NAME = "vike-trader"

# Default analytics endpoint for opt-in usage telemetry. EMPTY = telemetry stays OFF even if the
# user consents (there's nowhere to send). Set this to your vike.io receiver URL when you ship the
# app, or override per-process with the VIKE_TELEMETRY_URL env var. The endpoint must accept a POST
# of one JSON event per tool call: {ts_ms, client (anon uuid), tool, args (summary, no source),
# ok, error, duration_ms}.
DEFAULT_TELEMETRY_URL = "https://telemetry.vike.io/telemetry"


def telemetry_url() -> str:
    """The telemetry endpoint: VIKE_TELEMETRY_URL env override, else DEFAULT_TELEMETRY_URL."""
    return (os.environ.get("VIKE_TELEMETRY_URL") or DEFAULT_TELEMETRY_URL).strip()


# Shared token the local MCP server sends (x-vike-token) so the receiver can reject random internet
# spam. NOTE: this repo is PUBLIC, so this is NOT a real secret — it only deters bots that don't read
# the source. That's an accepted trade for low-value usage telemetry. Override per-machine with the
# VIKE_TELEMETRY_TOKEN env var. To rotate: change BOTH this value and the receiver's token, re-ship.
DEFAULT_TELEMETRY_TOKEN = "ad4255b553d56a57e74e4cdcc6e6ffce7953392c55bcded696be12f1bd7b7350"


def telemetry_token() -> str:
    """The telemetry token: VIKE_TELEMETRY_TOKEN env override, else the baked default."""
    return (os.environ.get("VIKE_TELEMETRY_TOKEN") or DEFAULT_TELEMETRY_TOKEN).strip()


def _python_exe() -> str:
    """The console interpreter to launch the stdio server (map pythonw.exe -> python.exe)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        cand = exe.with_name("python.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


def default_data_root() -> str:
    """Absolute path to this install's Parquet cache (what the running app uses)."""
    from ..data.cache import DEFAULT_ROOT

    return str(Path(DEFAULT_ROOT).resolve())


def server_env(data_root: str, *, telemetry: bool = False, telemetry_url: str | None = None) -> dict:
    """Environment for the MCP server process: absolute data root + optional telemetry."""
    env = {"VIKE_DATA_ROOT": str(Path(data_root).resolve())}
    if telemetry:
        env["VIKE_TELEMETRY"] = "1"
        tok = telemetry_token()
        if tok:
            env["VIKE_TELEMETRY_TOKEN"] = tok
    if telemetry_url:
        env["VIKE_TELEMETRY_URL"] = telemetry_url
    return env


def mcp_server_entry(data_root: str | None = None, **kw) -> dict:
    """The Claude-config entry for the vike-trader MCP server (command/args/env)."""
    return {
        "command": _python_exe(),
        "args": ["-m", "vike_trader_app.ai.mcp_server"],
        "env": server_env(data_root or default_data_root(), **kw),
    }


# --- Claude Desktop -------------------------------------------------------

def claude_desktop_config_path() -> Path:
    """Per-OS path to claude_desktop_config.json."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "Claude" / "claude_desktop_config.json"


def install_into_claude_desktop(data_root: str | None = None, *, path: str | Path | None = None, **kw) -> Path:
    """Merge the vike-trader server into Claude Desktop's config (preserving other servers/keys)."""
    cfg_path = Path(path) if path else claude_desktop_config_path()
    cfg: dict = {}
    if cfg_path.exists():
        try:
            loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg = loaded
        except (json.JSONDecodeError, OSError):
            cfg = {}
    servers = cfg.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = cfg["mcpServers"] = {}
    servers[SERVER_NAME] = mcp_server_entry(data_root, **kw)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg_path


# --- Claude Code (CLI) ----------------------------------------------------

def _quote(s: str) -> str:
    return f'"{s}"' if (" " in s or "\\" in s) else s


def claude_code_command(data_root: str | None = None, **kw) -> list[str]:
    """argv for `claude mcp add` registering the vike-trader server for Claude Code."""
    entry = mcp_server_entry(data_root, **kw)
    argv = ["claude", "mcp", "add", SERVER_NAME]
    for k, v in entry["env"].items():
        argv += ["--env", f"{k}={v}"]
    argv += ["--", entry["command"], *entry["args"]]
    return argv


def claude_code_command_str(data_root: str | None = None, **kw) -> str:
    """A copy-pasteable `claude mcp add ...` command line."""
    return " ".join(_quote(a) for a in claude_code_command(data_root, **kw))


def claude_code_available() -> bool:
    """True when the `claude` CLI is on PATH."""
    return shutil.which("claude") is not None


# --- launching Claude Desktop (best-effort) -------------------------------

def _claude_desktop_candidates() -> list[Path]:
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return [
            local / "AnthropicClaude" / "claude.exe",
            local / "Programs" / "claude" / "Claude.exe",
            local / "Programs" / "Claude" / "Claude.exe",
        ]
    if sys.platform == "darwin":
        return [Path("/Applications/Claude.app")]
    return []


def launch_claude_desktop() -> bool:
    """Best-effort: open the Claude Desktop app. Returns True if a launch was attempted."""
    if sys.platform == "darwin":
        app = Path("/Applications/Claude.app")
        if app.exists():
            subprocess.Popen(["open", str(app)])
            return True
        return False
    for exe in _claude_desktop_candidates():
        if exe.exists():
            subprocess.Popen([str(exe)])
            return True
    return False
