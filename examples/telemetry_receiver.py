"""Example vike.io telemetry receiver for the vike-trader MCP server.

The desktop app's LOCAL MCP server POSTs one JSON event per tool call to your
``VIKE_TELEMETRY_URL`` (see ``ai/telemetry.py``) — only when the user has opted in via the
Connect dialog. This is a minimal reference receiver: it validates each event and appends it
to a JSONL file. Point ``connect.DEFAULT_TELEMETRY_URL`` / ``VIKE_TELEMETRY_URL`` at the
deployed URL and you'll collect anonymous usage (tool name, an argument SUMMARY, timing —
never strategy source; the client id is a random per-install UUID).

Run locally:
    pip install "fastapi[standard]" uvicorn
    uvicorn examples.telemetry_receiver:app --host 0.0.0.0 --port 8000
    # then set the endpoint URL to:  https://<your-host>/telemetry

PRODUCTION TODO (deliberately out of scope for this example):
    * terminate TLS (HTTPS) in front of this; never collect over plain HTTP
    * cap body size + add rate limiting; reject oversized / malformed payloads
    * persist to a real DB / warehouse instead of a local file, and rotate it
    * optionally require a shared header token to deter spoofed events
    * honor retention + deletion requests (treat the anon client id as pseudonymous PII)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ValidationError

_SINK = Path(os.environ.get("VIKE_TELEMETRY_SINK", "telemetry-events.jsonl"))

app = FastAPI(title="vike-trader telemetry receiver")


class Event(BaseModel):
    """One MCP tool-call event (mirrors ai.telemetry.record's payload)."""

    ts_ms: int
    client: str
    tool: str
    args: dict = {}
    ok: bool = True
    error: str | None = None
    duration_ms: float | None = None


@app.post("/telemetry", status_code=204)
async def telemetry(request: Request) -> Response:
    """Validate one usage event and append it to the JSONL sink."""
    try:
        event = Event.model_validate(await request.json())
    except (ValidationError, json.JSONDecodeError, ValueError):
        return Response(status_code=400)
    _SINK.parent.mkdir(parents=True, exist_ok=True)
    with _SINK.open("a", encoding="utf-8") as f:
        f.write(event.model_dump_json() + "\n")
    return Response(status_code=204)


@app.get("/health")
async def health() -> dict:
    return {"ok": True}
