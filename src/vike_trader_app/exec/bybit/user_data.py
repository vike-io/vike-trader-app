"""Bybit private-WS open_ws coroutine + make_bybit_run_core factory.

open_bybit_user_data_ws:
  - Connects via the injectable ``connect`` factory (or lazy websockets.connect).
  - Sends auth frame (NEVER logged).
  - LOOPs recv until op=='auth' ack arrives (ignoring interleaved/unsolicited frames).
  - Sends subscribe frame.
  - LOOPs recv until op=='subscribe' ack arrives (ignoring interleaved frames).
  - Returns the ws object.

make_bybit_run_core:
  - Returns a synchronous run_core(emit, stop) that wraps run_user_data_forever with the
    Bybit open_ws, decoder, and Bybit-shaped ping frame.

Credentials NEVER appear in events, signals, or log messages.
"""
from __future__ import annotations

import asyncio
import json

from vike_trader_app.exec.bybit.ws_auth import build_auth_frame, build_subscribe_frame
from vike_trader_app.exec.bybit.mapper import map_bybit_private
from vike_trader_app.exec.user_data_core import run_user_data_forever, UserDataAuthError


async def open_bybit_user_data_ws(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    now_ms,
    connect=None,
    topics: tuple[str, ...] = ("execution", "order"),
):
    """Connect -> auth -> LOOP until op==auth ack -> subscribe -> LOOP until op==subscribe ack -> return ws.

    ``connect`` is an async callable ``(ws_url: str) -> ws`` injected for offline testing.
    When None, lazy-imports websockets and calls ``websockets.connect(ws_url)``.
    Credentials are NEVER logged or emitted.
    """
    if connect is None:
        import websockets  # noqa: PLC0415 — lazy import so websockets is optional at import time
        ws = await websockets.connect(ws_url)
    else:
        ws = await connect(ws_url)

    # Send auth frame — NEVER log it (contains api_key + signature)
    auth_frame = build_auth_frame(api_key=api_key, api_secret=api_secret, now_ms=now_ms)
    await ws.send(json.dumps(auth_frame))

    # LOOP until we see the auth ack (ignore interleaved/unsolicited frames)
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("op") == "auth":
            if not msg.get("success"):
                # NEVER include signature, secret, or auth frame args in the error message
                raise UserDataAuthError(
                    f"Bybit WS auth failed: {msg.get('ret_msg', '')}"
                )
            break

    # Send subscribe frame
    sub_frame = build_subscribe_frame(topics)
    await ws.send(json.dumps(sub_frame))

    # LOOP until we see the subscribe ack (ignore interleaved frames)
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("op") == "subscribe":
            if not msg.get("success"):
                raise UserDataAuthError(
                    f"Bybit WS subscribe failed: {msg.get('ret_msg', '')}"
                )
            break

    return ws


async def _bybit_ping(ws) -> None:
    """Send Bybit-shaped keepalive: {"req_id": "ping_1", "op": "ping"}."""
    await ws.send(json.dumps({"req_id": "ping_1", "op": "ping"}))


def make_bybit_run_core(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    symbol: str,
    now_ms,
    connect=None,
):
    """Return a synchronous run_core(emit, stop) that drives the Bybit private-WS fill stream.

    ``connect`` is passed through to ``open_bybit_user_data_ws`` for offline/unit testing.
    """

    def run_core(emit, stop):
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_bybit_user_data_ws(
                    ws_url=ws_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    now_ms=now_ms,
                    connect=connect,
                ),
                decode=lambda frame: map_bybit_private(frame, venue="bybit", symbol=symbol),
                ping=_bybit_ping,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
