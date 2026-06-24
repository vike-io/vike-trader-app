"""Qt-free venue-neutral user-data WS async core.

Takes a venue-supplied ``open_ws()`` coroutine (which connects, auths, and subscribes inside) and a
pure ``decode(frame)`` callback.  Runs a recv loop with a short recv-timeout, an optional app-level
ping keepalive, exponential-backoff reconnect on transport hiccups, and a clean stop path.  Raises
``UserDataAuthError`` immediately (no loop) on auth/protocol failures.  Emits decoded events via the
``emit`` callback.

This is the off-thread engine that venue-specific QThread workers wrap.
"""
from __future__ import annotations

import asyncio
import json
import time


class UserDataAuthError(RuntimeError):
    """Auth/protocol error — raise, do NOT reconnect-loop."""


def _default_now_ms() -> int:
    return int(time.monotonic() * 1000)


async def run_user_data_forever(
    emit,                      # callable(event) -> None  (the worker's report.emit)
    *,
    open_ws,                   # async callable() -> ws   (connects + auths + subscribes inside)
    decode,                    # callable(frame: dict) -> list[object]  (pure venue mapper)
    stop,                      # callable() -> bool
    ping=None,                 # async callable(ws) -> None  (send app-level keepalive); optional
    ping_ms: int = 20_000,
    now_ms=None,               # callable() -> int  (defaults to time.monotonic()*1000)
    recv_timeout: float = 1.0,
    max_backoff: float = 30.0,
) -> None:
    """Persistent user-data pump: open_ws -> recv loop -> decode -> emit, with keepalive + reconnect."""
    if now_ms is None:
        now_ms = _default_now_ms

    backoff = 1.0
    while not stop():
        try:
            ws = await open_ws()
            last_ping = now_ms()   # init right after connect — mirrors binance/user_data.py
            try:
                while not stop():
                    if ping is not None and now_ms() - last_ping >= ping_ms:
                        await ping(ws)
                        last_ping = now_ms()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                    except (asyncio.TimeoutError, TimeoutError):
                        continue  # idle — loop back to poll stop() / ping cadence
                    try:
                        frame = json.loads(raw)
                    except (ValueError, TypeError):
                        continue  # non-JSON keepalive (e.g. OKX text 'pong') — skip, do NOT reconnect
                    for event in decode(frame):
                        emit(event)
            finally:
                # BOUNDED close: a busy stream (e.g. Binance futures markPrice updates) can stall the
                # websockets close handshake up to the lib default (~10s), which would outlast the
                # worker's wait(2000)=2s teardown join -> 0xC0000409. Abandon a slow close after
                # recv_timeout; the socket is reclaimed when the loop/ws is finalized.
                try:
                    await asyncio.wait_for(ws.close(), timeout=recv_timeout)
                except Exception:  # noqa: BLE001 — close errors / timeout must not block teardown
                    pass
            backoff = 1.0
        except UserDataAuthError:
            raise
        except Exception:  # noqa: BLE001 — transport hiccup -> reconnect with backoff
            if stop():
                break
            slept = 0.0
            while slept < backoff and not stop():   # wake every recv_timeout to poll stop()
                await asyncio.sleep(min(recv_timeout, backoff - slept))
                slept += recv_timeout
            if stop():
                break
            backoff = min(backoff * 2, max_backoff)
