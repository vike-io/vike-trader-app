"""Opt-in usage telemetry for the MCP tools.

Records each tool call (name, timing, ok/error, a SAFE arg summary) so the app
publisher can analyze how the AI tools are used — WITHOUT routing the MCP traffic
through the cloud and WITHOUT changing who pays for inference (the user's own
Claude subscription still bills the model). The local MCP server already sees every
tool call; this just logs it and, optionally, reports it.

Privacy-first:
  * OFF by default — nothing is recorded unless explicitly enabled.
  * Strategy SOURCE is never sent: only a sha256 prefix + length.
  * The client id is a random per-install UUID (no machine name, no PII).
  * Remote reporting is best-effort on a background thread and can never break,
    slow, or fail a tool call.

Enable via environment (the app publisher sets these when shipping):
    VIKE_TELEMETRY=1                  # turn on (writes a local JSONL log)
    VIKE_TELEMETRY_URL=https://...    # also POST each event to this endpoint
    VIKE_TELEMETRY_DIR=storage/telemetry   # where the local log + client id live
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

_SCALAR = (str, int, float, bool, type(None))
# Argument names whose VALUE is sensitive (user source) — replaced with a sha+len.
_SOURCE_ARGS = ("strategy_code",)


def _log_dir() -> Path:
    return Path(os.environ.get("VIKE_TELEMETRY_DIR", "storage/telemetry"))


def enabled() -> bool:
    """True when telemetry is switched on (default OFF)."""
    return os.environ.get("VIKE_TELEMETRY", "").strip().lower() in ("1", "true", "on", "yes")


def _endpoint() -> str | None:
    url = os.environ.get("VIKE_TELEMETRY_URL", "").strip()
    return url or None


def _client_id() -> str:
    """Stable anonymous id for this install (random UUID persisted locally; no PII)."""
    p = _log_dir() / "client_id"
    try:
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        cid = uuid.uuid4().hex
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cid, encoding="utf-8")
        return cid
    except OSError:
        return "anonymous"


def _safe_args(kwargs: dict) -> dict:
    """Whitelist scalars; replace strategy source with sha+len; summarize collections."""
    out: dict = {}
    for k, v in kwargs.items():
        if k in _SOURCE_ARGS and isinstance(v, str):
            out["strategy_code_sha"] = hashlib.sha256(v.encode("utf-8")).hexdigest()[:12]
            out["strategy_code_len"] = len(v)
        elif isinstance(v, _SCALAR):
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = {"_len": len(v)}
        elif isinstance(v, dict):
            out[k] = {"_keys": sorted(map(str, v.keys()))[:20]}
        else:
            out[k] = f"<{type(v).__name__}>"
    return out


def _post(event: dict, url: str) -> None:
    """Best-effort POST of one event; all errors swallowed (telemetry is never load-bearing)."""
    try:
        import urllib.request

        data = json.dumps(event).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass


def record(event: dict) -> None:
    """Append ``event`` to the local JSONL log and (if configured) POST it — best-effort."""
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / "mcp-usage.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        pass
    url = _endpoint()
    if url:
        threading.Thread(target=_post, args=(event, url), daemon=True).start()


def instrument(fn):
    """Wrap an MCP tool ``fn`` to record one telemetry event per call (no-op when disabled).

    ``functools.wraps`` preserves the wrapped function's name/signature/annotations, so FastMCP
    still derives the correct JSON schema from it. When telemetry is off the wrapper adds nothing
    but a single boolean check.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not enabled():
            return fn(*args, **kwargs)
        t0 = time.monotonic()
        ok, err = True, None
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            ok, err = False, type(e).__name__
            raise
        finally:
            record({
                "ts_ms": int(time.time() * 1000),
                "client": _client_id(),
                "tool": fn.__name__,
                "args": _safe_args(kwargs),
                "ok": ok,
                "error": err,
                "duration_ms": round((time.monotonic() - t0) * 1000, 1),
            })

    return wrapper
