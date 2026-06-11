"""vike-trader telemetry receiver (hardened) — runs on prod1 behind nginx/TLS.

Accepts the anonymous usage events POSTed by the desktop app's local MCP server (ai/telemetry.py).
Validation + body cap here; TLS + rate limiting are handled by the nginx vhost in front.

Env:
    VIKE_TELEMETRY_SINK   JSONL output path (default /var/lib/vike-telemetry/events.jsonl)
    VIKE_TELEMETRY_TOKEN  optional shared secret; when set, requests must send X-Vike-Token
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ValidationError

_SINK = Path(os.environ.get("VIKE_TELEMETRY_SINK", "/var/lib/vike-telemetry/events.jsonl"))
_TOKEN = os.environ.get("VIKE_TELEMETRY_TOKEN", "")
_MAX_BODY = 8192

app = FastAPI(title="vike-trader telemetry receiver")


class Event(BaseModel):
    ts_ms: int
    client: str
    tool: str
    args: dict = {}
    ok: bool = True
    error: str | None = None
    duration_ms: float | None = None


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/telemetry", status_code=204)
async def telemetry(request: Request) -> Response:
    if _TOKEN and request.headers.get("x-vike-token") != _TOKEN:
        return Response(status_code=401)
    body = await request.body()
    if len(body) > _MAX_BODY:
        return Response(status_code=413)
    try:
        event = Event.model_validate_json(body)
    except (ValidationError, ValueError):
        return Response(status_code=400)
    _SINK.parent.mkdir(parents=True, exist_ok=True)
    with _SINK.open("a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")
    return Response(status_code=204)
