"""Deribit private-WS open_ws coroutine + make_deribit_run_core factory.

open_deribit_user_data_ws:
  - Connects via the injectable ``connect`` factory (or a bare lazy websockets.connect — Deribit's WS
    is NOT Cloudflare-1010-gated, so NO browser UA, unlike okx/user_data.py).
  - Sends the public/auth client_credentials frame (NEVER logged — carries client_id + client_secret).
  - LOOPs recv until the auth response (matched by JSON-RPC request id) arrives, capturing
    access_token + refresh_token into ``token_cell`` (the refresh ping needs refresh_token).
  - Sends the private/subscribe frame for the user.trades channel(s).
  - LOOPs recv until the subscribe response (matched by id) arrives.
  - Each recv is bounded by ``recv_timeout`` to poll ``stop()`` + an overall ``handshake_timeout``
    deadline so a half-open/stalled handshake cannot hang the worker thread (0xC0000409 fix).
  - Closes the ws on ANY handshake exit; returns the ws on success.

make_deribit_run_core:
  - Returns a synchronous run_core(emit, stop) wrapping run_user_data_forever with the Deribit
    open_ws, decode=map_deribit_private, and the token-refresh ping hook (ping_ms=600_000).

Deribit differences from OKX:
  - JSON-RPC: acks are correlated by request id (rpc.parse_response), not event=='login'/'subscribe'.
  - Auth grant = public/auth client_credentials (client_id + client_secret in the frame).
  - Failure: parse_response(frame)[2] (the {code,message} error dict) -> UserDataAuthError (NO creds).
  - Ping = a NON-blocking await ws.send(public/auth refresh_token) — the OKX-class ping, NOT Binance's
    blocking listenKey PUT (no asyncio.to_thread). The hook swallows errors so it never thrashes the
    reconnect loop; a dead refresh_token self-heals via run_user_data_forever's re-auth on reconnect.
  - ping_ms=600_000 (10 min), comfortably below a conservative token lifetime (live tokens are large).
  - NO public/set_heartbeat (would obligate a public/test responder; the recv loop + refresh suffice).

Credentials NEVER appear in events, signals, or log messages.
"""
from __future__ import annotations

import asyncio
import json
import logging

from vike_trader_app.exec.deribit.mapper import map_deribit_private
from vike_trader_app.exec.deribit.rpc import JsonRpcBuilder, parse_response
from vike_trader_app.exec.deribit.ws_auth import (
    build_client_credentials_auth,
    build_private_subscribe,
    build_refresh_token_auth,
)
from vike_trader_app.exec.user_data_core import UserDataAuthError, run_user_data_forever

_log = logging.getLogger("vike.exec")


class _HandshakeStopped(Exception):
    """stop() turned True during the handshake — run_user_data_forever's reconnect guard breaks cleanly."""


async def _await_ack(ws, *, rid, stop, recv_timeout, handshake_timeout, now_ms) -> dict:
    """Recv until a JSON-RPC response with id==rid arrives; wake every recv_timeout to poll stop()
    and the overall deadline. Returns the ``result`` dict on success.

    Success: parse_response(frame) yields (rid, result, None) -> return result.
    Failure: (rid, None, error) -> UserDataAuthError(f"Deribit auth failed: {error.get('message')}")
             (NEVER include client_id/client_secret/tokens).
    Deadline: UserDataAuthError("Deribit WS ack timed out").
    stop():   _HandshakeStopped.
    Interleaved subscription notifications / non-JSON keepalives: skipped (continue).
    """
    deadline = now_ms() + int(handshake_timeout * 1000)
    while True:
        if stop is not None and stop():
            raise _HandshakeStopped()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            if now_ms() >= deadline:
                raise UserDataAuthError("Deribit WS ack timed out")  # NEVER include creds
            continue
        try:
            frame = json.loads(raw)
        except (ValueError, TypeError):
            continue  # non-JSON keepalive (e.g. raw 'pong') — skip
        fid, result, error = parse_response(frame)
        if fid != rid:
            continue  # interleaved notification or a different reply — keep looping
        if error is not None:
            # NEVER include credentials in the error message
            raise UserDataAuthError(f"Deribit auth failed: {error.get('message', '')}")
        return result or {}


async def open_deribit_user_data_ws(
    *,
    ws_url: str,
    client_id: str,
    client_secret: str,
    channels: list[str],
    now_ms,
    builder: JsonRpcBuilder | None = None,
    token_cell: dict | None = None,
    connect=None,
    scope: str | None = None,
    stop=None,
    recv_timeout: float = 1.0,
    handshake_timeout: float = 10.0,
):
    """Connect -> public/auth -> AWAIT auth result -> private/subscribe -> AWAIT subscribe result
    -> return ws. Closes the ws on ANY handshake exit. Credentials NEVER logged.

    ``builder`` (JsonRpcBuilder) hands out the request ids; ``token_cell`` (mutable dict) receives
    access_token + refresh_token from the auth result so the refresh ping can read the latest token.
    ``connect`` is an async callable ``(ws_url) -> ws`` injected for offline testing; when None, a
    bare lazy ``websockets.connect(ws_url, open_timeout=10)`` is used (Deribit needs no browser UA).
    """
    if builder is None:
        builder = JsonRpcBuilder()
    if token_cell is None:
        token_cell = {}

    if connect is None:
        import websockets  # noqa: PLC0415 — lazy import so websockets is optional at import time
        ws = await websockets.connect(ws_url, open_timeout=10)
    else:
        ws = await connect(ws_url)
    try:
        # public/auth client_credentials — NEVER log this frame (carries client_id + client_secret)
        auth_id = builder.next_id()
        auth_frame = build_client_credentials_auth(
            client_id=client_id, client_secret=client_secret, scope=scope, rpc_id=auth_id)
        await ws.send(json.dumps(auth_frame))
        result = await _await_ack(ws, rid=auth_id, stop=stop, recv_timeout=recv_timeout,
                                  handshake_timeout=handshake_timeout, now_ms=now_ms)
        # Capture tokens for the refresh ping (NEVER logged).
        token_cell["access_token"] = result.get("access_token", "")
        token_cell["refresh_token"] = result.get("refresh_token", "")

        # private/subscribe to the user.trades channel(s)
        sub_id = builder.next_id()
        sub_frame = build_private_subscribe(channels=channels, rpc_id=sub_id)
        await ws.send(json.dumps(sub_frame))
        await _await_ack(ws, rid=sub_id, stop=stop, recv_timeout=recv_timeout,
                         handshake_timeout=handshake_timeout, now_ms=now_ms)
    except BaseException:
        await ws.close()  # close the socket if stop/auth/deadline fires mid-handshake
        raise

    return ws


def _refresh_token(*, builder: JsonRpcBuilder, token_cell: dict):
    """Return an async ping(ws) that sends public/auth grant_type=refresh_token using the cell's token.

    NON-blocking await ws.send (the OKX-class ping; no asyncio.to_thread). Swallows + logs (WITHOUT
    creds) any error so a transient send failure does not thrash run_user_data_forever's reconnect.
    The refresh response (a later RPC reply on the same socket) may carry a rotated refresh_token;
    the design tolerates rotation via token_cell, but the hook does not block awaiting the reply.
    """
    async def _ping(ws) -> None:
        refresh = token_cell.get("refresh_token", "")
        if not refresh:
            return
        try:
            frame = build_refresh_token_auth(refresh_token=refresh, rpc_id=builder.next_id())
            await ws.send(json.dumps(frame))  # NEVER log frame (carries refresh_token)
        except Exception:  # noqa: BLE001 — a raised ping would thrash the reconnect loop
            _log.warning("Deribit token-refresh send failed; reconnect path will re-auth")
    return _ping


def make_deribit_run_core(
    *,
    ws_url: str,
    client_id: str,
    client_secret: str,
    symbol: str,
    currency: str,
    now_ms,
    connect=None,
    kind: str = "option",
    interval: str = "raw",
    scope: str | None = None,
):
    """Return a synchronous run_core(emit, stop) that drives the Deribit private-WS fill stream.

    Subscribes to ``user.trades.{kind}.{currency}.{interval}`` (default option/<currency>/raw).
    ``connect`` is passed through to open_deribit_user_data_ws for offline/unit testing.
    ping_ms=600_000 (10 min) refreshes the access token well before any conservative lifetime.
    """
    channel = f"user.trades.{kind}.{currency}.{interval}"

    def run_core(emit, stop):
        builder = JsonRpcBuilder()
        token_cell: dict = {}
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_deribit_user_data_ws(
                    ws_url=ws_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    channels=[channel],
                    now_ms=now_ms,
                    builder=builder,
                    token_cell=token_cell,
                    connect=connect,
                    scope=scope,
                    stop=stop,
                    recv_timeout=1.0,
                ),
                decode=lambda frame: map_deribit_private(frame, venue="deribit", symbol=symbol),
                ping=_refresh_token(builder=builder, token_cell=token_cell),
                ping_ms=600_000,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
