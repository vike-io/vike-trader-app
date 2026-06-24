"""Bybit private-WS open_ws coroutine + make_bybit_run_core factory.

open_bybit_user_data_ws:
  - Connects via the injectable ``connect`` factory (or lazy websockets.connect).
  - Sends auth frame (NEVER logged).
  - LOOPs recv until op=='auth' ack arrives (ignoring interleaved/unsolicited frames),
    bounding each recv with ``recv_timeout`` to poll ``stop()`` and an overall ``handshake_timeout``
    deadline so a half-open/stalled handshake cannot hang the worker thread (0xC0000409 fix).
  - Sends subscribe frame.
  - LOOPs recv until op=='subscribe' ack arrives (same bounded-recv guarantees).
  - Closes the ws on ANY handshake exit and returns the ws object on success.

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


class _HandshakeStopped(Exception):
    """stop() turned True during the handshake — run_user_data_forever's reconnect guard breaks cleanly."""


async def _await_ack(ws, *, op, stop, recv_timeout, handshake_timeout, now_ms):
    """Recv until the op-ack arrives, waking every recv_timeout to poll stop() and the overall deadline."""
    deadline = now_ms() + int(handshake_timeout * 1000)
    while True:
        if stop is not None and stop():
            raise _HandshakeStopped()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            if now_ms() >= deadline:
                raise UserDataAuthError(f"Bybit WS {op} ack timed out")  # NEVER include creds
            continue
        msg = json.loads(raw)
        if msg.get("op") == op:
            if not msg.get("success"):
                # NEVER include signature, secret, or auth frame args in the error message
                raise UserDataAuthError(f"Bybit WS {op} failed: {msg.get('ret_msg', '')}")
            return


async def open_bybit_user_data_ws(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    now_ms,
    connect=None,
    topics: tuple[str, ...] = ("execution", "order"),
    stop=None,
    recv_timeout: float = 1.0,
    handshake_timeout: float = 10.0,
):
    """Connect -> auth -> LOOP until op==auth ack -> subscribe -> LOOP until op==subscribe ack -> return ws.

    ``connect`` is an async callable ``(ws_url: str) -> ws`` injected for offline testing.
    When None, lazy-imports websockets and calls ``websockets.connect(ws_url)``.
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
        # Send auth frame — NEVER log it (contains api_key + signature)
        auth_frame = build_auth_frame(api_key=api_key, api_secret=api_secret, now_ms=now_ms)
        await ws.send(json.dumps(auth_frame))
        await _await_ack(ws, op="auth", stop=stop, recv_timeout=recv_timeout,
                         handshake_timeout=handshake_timeout, now_ms=now_ms)

        # Send subscribe frame
        sub_frame = build_subscribe_frame(topics)
        await ws.send(json.dumps(sub_frame))
        await _await_ack(ws, op="subscribe", stop=stop, recv_timeout=recv_timeout,
                         handshake_timeout=handshake_timeout, now_ms=now_ms)
    except BaseException:
        await ws.close()   # ensure the socket closes if stop/auth/deadline fires mid-handshake
        raise

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
                    stop=stop,
                    recv_timeout=1.0,
                ),
                decode=lambda frame: map_bybit_private(frame, venue="bybit", symbol=symbol),
                ping=_bybit_ping,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
