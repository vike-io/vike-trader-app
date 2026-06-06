"""vike.io telemetry receiver for the vike-trader MCP server (hardened reference).

The desktop app's LOCAL MCP server POSTs one JSON event per tool call to your
``VIKE_TELEMETRY_URL`` (see ``ai/telemetry.py``) — only when the user has opted in via the
Connect dialog. This receiver validates each event, enforces a body-size cap, optionally checks
a shared token, and appends to a JSONL sink. The client id is a random per-install UUID and the
strategy source is never sent (only a sha + length), so events are anonymous/pseudonymous.

Environment:
    VIKE_TELEMETRY_SINK   JSONL output path (default /var/lib/vike-telemetry/events.jsonl)
    VIKE_TELEMETRY_TOKEN  optional shared secret; when set, requests must carry header
                          ``X-Vike-Token: <token>`` (the client must be built to send it)

Run locally:
    pip install fastapi "uvicorn[standard]" pydantic
    uvicorn examples.telemetry_receiver:app --host 127.0.0.1 --port 8099

Production (prod1: Ubuntu 24.04, nginx, no Docker) — bind localhost, front with nginx for TLS:
    * systemd unit runs `uvicorn app:app --host 127.0.0.1 --port 8099` as a non-root user
    * an isolated nginx vhost for telemetry.vike.io reverse-proxies to 127.0.0.1:8099 with a
      `limit_req` rate limit + `client_max_body_size 8k`
    * certbot --nginx -d telemetry.vike.io issues the Let's Encrypt cert
  TLS and rate limiting are handled by nginx; this app handles validation, the body cap, and the
  optional token. Persist to a real DB/warehouse (this server already runs Postgres/ClickHouse)
  and honor retention/deletion when you move past the JSONL sink.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ValidationError

_SINK = Path(os.environ.get("VIKE_TELEMETRY_SINK", "/var/lib/vike-telemetry/events.jsonl"))
_TOKEN = os.environ.get("VIKE_TELEMETRY_TOKEN", "")
_MAX_BODY = 8192  # bytes — one event is well under this; reject anything larger

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


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/telemetry", status_code=204)
async def telemetry(request: Request) -> Response:
    """Validate one usage event and append it to the JSONL sink (token + size gated)."""
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
