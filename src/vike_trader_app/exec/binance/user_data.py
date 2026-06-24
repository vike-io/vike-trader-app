"""Binance WS-API private-stream open_ws coroutine + make_binance_run_core factory.

open_binance_user_data_ws:
  - Connects via the injectable ``connect`` factory (or lazy websockets.connect — bare, no UA header).
  - Sends ONE signed subscribe request (build_subscribe_request) — NEVER logged (carries apiKey +
    signature derived from api_secret).
  - LOOPs recv until the id-matched status==200 ACK arrives (ignoring interleaved/unsolicited frames,
    tolerating raw non-JSON text), bounding each recv with ``recv_timeout`` to poll ``stop()`` and
    an overall ``handshake_timeout`` deadline so a half-open/stalled handshake cannot hang the worker
    thread (0xC0000409 fix).
  - status != 200 OR 'error' present -> UserDataAuthError (NEVER include api_secret).
  - Closes the ws on ANY handshake exit and returns the ws object on success.

make_binance_run_core:
  - Returns a synchronous run_core(emit, stop) that wraps run_user_data_forever with the Binance
    open_ws, decode=map_binance_private, and ping=None (the Binance WS-API server sends 20-second
    WS protocol PING control frames; the ``websockets`` library auto-pongs them, so NO app-level
    ping is needed).

Credentials NEVER appear in events, signals, or log messages.
"""
from __future__ import annotations

import asyncio
import json

from vike_trader_app.exec.binance.ws_auth import build_subscribe_request
from vike_trader_app.exec.binance.mapper import map_binance_private
from vike_trader_app.exec.user_data_core import run_user_data_forever, UserDataAuthError


class _HandshakeStopped(Exception):
    """stop() turned True during the handshake — run_user_data_forever's reconnect guard breaks cleanly."""


async def _await_subscribe_ack(ws, *, req_id, stop, recv_timeout, handshake_timeout, now_ms) -> None:
    """Recv until {'id': req_id, 'status': 200} arrives; wake every recv_timeout to poll stop()
    and the overall deadline.

    Success: msg.get('id') == req_id AND msg.get('status') == 200.
    Failure: status != 200 OR 'error' present -> UserDataAuthError(error.msg only, NEVER creds).
    Deadline: UserDataAuthError('Binance WS subscribe ack timed out').
    stop():   _HandshakeStopped.
    Non-JSON / non-dict / id-mismatch frames -> ignored (keep looping).
    """
    deadline = now_ms() + int(handshake_timeout * 1000)
    while True:
        if stop is not None and stop():
            raise _HandshakeStopped()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            if now_ms() >= deadline:
                raise UserDataAuthError("Binance WS subscribe ack timed out")  # NEVER include creds
            continue
        # Tolerate non-JSON frames (e.g. raw text keepalive)
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue  # non-JSON frame — skip
        if not isinstance(msg, dict):
            continue
        # Only act on frames with our request id
        if msg.get("id") != req_id:
            continue  # id-mismatch: interleaved frame — skip
        # Check for failure: non-200 status OR 'error' key present
        if msg.get("status") != 200 or "error" in msg:
            error = msg.get("error", {})
            if not isinstance(error, dict):
                error = {}
            # NEVER include credentials (api_key, api_secret, signature) in the error message
            raise UserDataAuthError(f"Binance WS subscribe failed: {error.get('msg', '')}")
        # status == 200 and no error: success
        return


async def open_binance_user_data_ws(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    now_ms,
    connect=None,
    recv_window: int = 5000,
    stop=None,
    recv_timeout: float = 1.0,
    handshake_timeout: float = 10.0,
):
    """Connect -> send signed subscribe request -> LOOP until id-matched status==200 ack -> return ws.

    ``connect`` is an async callable ``(ws_url: str) -> ws`` injected for offline testing.
    When None, lazy-imports websockets and calls ``websockets.connect(ws_url, open_timeout=10)``
    (bare — Binance demo is not Cloudflare-gated, so no browser UA header needed).
    Each handshake recv is bounded by ``recv_timeout`` (to poll ``stop()``) and an overall
    ``handshake_timeout`` deadline; the ws is closed on ANY handshake exit. Credentials are
    NEVER logged or emitted.
    """
    if connect is None:
        import websockets  # noqa: PLC0415 — lazy import so websockets is optional at import time
        ws = await websockets.connect(ws_url, open_timeout=10)
    else:
        ws = await connect(ws_url)
    try:
        # Build signed subscribe request — NEVER log it (contains apiKey + signature)
        request = build_subscribe_request(
            api_key=api_key,
            api_secret=api_secret,
            now_ms=now_ms,
            recv_window=recv_window,
        )
        req_id = request["id"]
        await ws.send(json.dumps(request))
        await _await_subscribe_ack(
            ws,
            req_id=req_id,
            stop=stop,
            recv_timeout=recv_timeout,
            handshake_timeout=handshake_timeout,
            now_ms=now_ms,
        )
    except BaseException:
        await ws.close()  # ensure the socket closes if stop/auth/deadline fires mid-handshake
        raise

    return ws


def make_binance_run_core(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    now_ms,
    connect=None,
):
    """Return a synchronous run_core(emit, stop) that drives the Binance WS-API fill stream.

    ``connect`` is passed through to ``open_binance_user_data_ws`` for offline/unit testing.
    ping=None: the Binance WS-API server sends 20-second WS protocol PING control frames;
    the ``websockets`` library auto-pongs them so no app-level ping is needed.
    """

    def run_core(emit, stop):
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_binance_user_data_ws(
                    ws_url=ws_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    now_ms=now_ms,
                    connect=connect,
                    stop=stop,
                    recv_timeout=1.0,
                ),
                decode=lambda frame: map_binance_private(frame, venue="binance", symbol=symbol),
                ping=None,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
