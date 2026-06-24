"""OKX private-WS open_ws coroutine + make_okx_run_core factory.

open_okx_user_data_ws:
  - Connects via the injectable ``connect`` factory (or lazy websockets.connect with browser UA).
  - Sends login frame (NEVER logged — carries apiKey + passphrase + sign).
  - LOOPs recv until event=='login' with code=='0' arrives (ignoring interleaved frames,
    tolerating raw non-JSON text like 'pong'), bounding each recv with ``recv_timeout`` to
    poll ``stop()`` and an overall ``handshake_timeout`` deadline so a half-open/stalled
    handshake cannot hang the worker thread (0xC0000409 fix).
  - Sends subscribe frame.
  - LOOPs recv until event=='subscribe' ack arrives (same bounded-recv guarantees).
  - Closes the ws on ANY handshake exit and returns the ws object on success.

make_okx_run_core:
  - Returns a synchronous run_core(emit, stop) that wraps run_user_data_forever with the
    OKX open_ws, decoder, and OKX-shaped text ping ('ping' raw string, not JSON).

OKX differences from Bybit:
  - Login uses build_login_frame(…, now_s=lambda: now_ms()//1000) (SECONDS, not ms).
  - Ack check: msg.get('event') == event AND str(msg.get('code','0')) == '0'.
  - Failure: event=='error' or non-'0' code -> UserDataAuthError (NO creds).
  - Ping: await ws.send('ping')  — raw text frame; server replies with raw 'pong'.
  - ping_ms=15_000 (well under OKX's 30s idle timeout).
  - Real connect uses additional_headers (websockets 16.0) with browser User-Agent
    to avoid Cloudflare 1010 error on the demo endpoint.
  - _await_ack tolerates JSONDecodeError on raw 'pong' inside the handshake recv loop.

Credentials NEVER appear in events, signals, or log messages.
"""
from __future__ import annotations

import asyncio
import json

from vike_trader_app.exec.okx.ws_auth import build_login_frame, build_subscribe_frame
from vike_trader_app.exec.okx.mapper import map_okx_private
from vike_trader_app.exec.user_data_core import run_user_data_forever, UserDataAuthError

# Browser User-Agent copied from okx/transport._BROWSER_HEADERS to bypass Cloudflare 1010
# on the OKX demo WebSocket endpoint (the same UA the REST transport uses).
_OKX_WS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


class _HandshakeStopped(Exception):
    """stop() turned True during the handshake — run_user_data_forever's reconnect guard breaks cleanly."""


async def _await_ack(ws, *, event, stop, recv_timeout, handshake_timeout, now_ms) -> None:
    """Recv until {'event': <event>} with code=='0' arrives; wake every recv_timeout to poll stop()
    and the overall deadline.

    Success: msg.get('event') == event AND str(msg.get('code', '0')) == '0'.
    Failure: msg.get('event') == 'error' OR str(msg.get('code', '0')) != '0'
             -> UserDataAuthError (NEVER include creds).
    Deadline: UserDataAuthError(f"OKX WS {event} ack timed out").
    stop():   _HandshakeStopped.
    Non-JSON frames (e.g. raw 'pong'): skipped (continue).
    """
    deadline = now_ms() + int(handshake_timeout * 1000)
    while True:
        if stop is not None and stop():
            raise _HandshakeStopped()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
        except (asyncio.TimeoutError, TimeoutError):
            if now_ms() >= deadline:
                raise UserDataAuthError(f"OKX WS {event} ack timed out")  # NEVER include creds
            continue
        # Tolerate non-JSON frames (e.g. OKX raw 'pong' reply during handshake)
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue  # non-JSON keepalive — skip
        if not isinstance(msg, dict):
            continue
        # Check for success ack
        if msg.get("event") == event and str(msg.get("code", "0")) == "0":
            return
        # Check for failure
        if msg.get("event") == "error" or str(msg.get("code", "0")) != "0":
            if msg.get("event") in (event, "error"):
                # NEVER include credentials in the error message
                raise UserDataAuthError(f"OKX WS {event} failed: {msg.get('msg', '')}")
        # Any other frame: ignore and keep looping


async def open_okx_user_data_ws(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
    now_ms,
    connect=None,
    inst_type: str = "SPOT",
    stop=None,
    recv_timeout: float = 1.0,
    handshake_timeout: float = 10.0,
    extra_headers: dict | None = None,
):
    """Connect -> login -> LOOP until event=='login' -> subscribe -> LOOP until event=='subscribe'
    -> return ws. Closes the ws on ANY handshake exit. Credentials NEVER logged.

    ``connect`` is an async callable ``(ws_url: str) -> ws`` injected for offline testing.
    When None, lazy-imports websockets and calls websockets.connect with the browser UA headers
    (``additional_headers``, websockets 16.0 API) to avoid Cloudflare 1010 on the OKX demo WS.
    ``extra_headers`` (the public parameter name) overrides _OKX_WS_HEADERS when provided.
    """
    if connect is None:
        import websockets  # noqa: PLC0415 — lazy import so websockets is optional at import time
        ws = await websockets.connect(
            ws_url,
            open_timeout=10,
            additional_headers=extra_headers if extra_headers is not None else _OKX_WS_HEADERS,
        )
    else:
        ws = await connect(ws_url)
    try:
        # Send login frame — NEVER log it (contains apiKey + passphrase + sign)
        login_frame = build_login_frame(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            now_s=lambda: now_ms() // 1000,  # OKX WS login wants SECONDS, not ms
        )
        await ws.send(json.dumps(login_frame))
        await _await_ack(ws, event="login", stop=stop, recv_timeout=recv_timeout,
                         handshake_timeout=handshake_timeout, now_ms=now_ms)

        # Send subscribe frame
        sub_frame = build_subscribe_frame(inst_type)
        await ws.send(json.dumps(sub_frame))
        await _await_ack(ws, event="subscribe", stop=stop, recv_timeout=recv_timeout,
                         handshake_timeout=handshake_timeout, now_ms=now_ms)
    except BaseException:
        await ws.close()  # ensure the socket closes if stop/auth/deadline fires mid-handshake
        raise

    return ws


async def _okx_ping(ws) -> None:
    """Send OKX keepalive: raw text 'ping' (NOT JSON). Server replies with raw text 'pong'."""
    await ws.send("ping")


def make_okx_run_core(
    *,
    ws_url: str,
    api_key: str,
    api_secret: str,
    passphrase: str,
    symbol: str,
    now_ms,
    connect=None,
    inst_type: str = "SPOT",
):
    """Return a synchronous run_core(emit, stop) that drives the OKX private-WS fill stream.

    ``connect`` is passed through to ``open_okx_user_data_ws`` for offline/unit testing.
    ``inst_type`` is forwarded to ``open_okx_user_data_ws`` (default "SPOT" keeps existing callers
    byte-identical). Pass ``inst_type="SWAP"`` for the perp variant (use ``make_okx_perp_run_core``).
    ping_ms=15_000 is well under OKX's 30s idle timeout.
    """

    def run_core(emit, stop):
        asyncio.run(
            run_user_data_forever(
                emit=emit,
                open_ws=lambda: open_okx_user_data_ws(
                    ws_url=ws_url,
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    now_ms=now_ms,
                    connect=connect,
                    stop=stop,
                    recv_timeout=1.0,
                    inst_type=inst_type,
                ),
                decode=lambda frame: map_okx_private(frame, venue="okx", symbol=symbol),
                ping=_okx_ping,
                ping_ms=15_000,
                stop=stop,
                recv_timeout=1.0,
                now_ms=now_ms,
            )
        )

    return run_core
