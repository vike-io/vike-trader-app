"""Qt-free user-data WS async core (mirrors data.vike_live.LiveBarFeed.run_forever).

Mints a listenKey, opens the WS, decodes executionReport frames OFF-thread and emit()s the mapped
frozen vike events. Binance specifics live inside the worker's own async loop (network-only):
listenKey mint, keepalive PUT on a cadence, auto-handling of idle via a recv timeout. RAISES on an
auth/protocol error (don't loop); reconnects with exponential backoff on a transport hiccup; CLOSES
the socket in finally so a stop() actually unblocks an idle stream.
"""

from __future__ import annotations

import asyncio
import json


class UserDataAuthError(RuntimeError):
    """Auth/protocol error — raise, do not loop."""


async def run_user_data_forever(emit, *, venue, symbol, mint_listen_key, keepalive, open_ws,
                                stop, now_ms, keepalive_ms: int = 1_800_000,
                                recv_timeout: float = 30.0, max_backoff: float = 30.0) -> None:
    """Persistent user-data pump: listenKey -> WS -> map -> emit, with keepalive + reconnect."""
    backoff = 1.0
    while not stop():
        try:
            listen_key = mint_listen_key()
            ws = await open_ws(listen_key)
            last_keepalive = now_ms()
            try:
                while not stop():
                    if now_ms() - last_keepalive >= keepalive_ms:
                        keepalive(listen_key)
                        last_keepalive = now_ms()
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=recv_timeout)
                    except (asyncio.TimeoutError, TimeoutError):
                        continue  # idle — loop back to poll stop()/keepalive
                    frame = json.loads(raw)
                    for event in _map(frame, venue=venue, symbol=symbol):
                        emit(event)
            finally:
                await ws.close()
            backoff = 1.0
        except UserDataAuthError:
            raise
        except Exception:  # noqa: BLE001 - transport hiccup -> reconnect with backoff
            if stop():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def _map(frame, *, venue, symbol):
    from vike_trader_app.exec.binance.mapper import map_execution_report

    if frame.get("e") == "executionReport":
        return map_execution_report(frame, venue=venue, symbol=symbol)
    return []
