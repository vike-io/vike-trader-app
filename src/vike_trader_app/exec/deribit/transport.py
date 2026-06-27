"""DeribitOrderTransport — a persistent, authed JSON-RPC-over-WS ORDER transport.

The LIVE drop-in for the 6a injected ``transport(method, params) -> dict`` seam
(DeribitExecutionClient.__init__). ONE websockets socket + ONE dedicated asyncio event
loop, both owned by this instance and driven SYNCHRONOUSLY on the MAIN thread:

  connect()            -> new_event_loop() + open ws + public/auth client_credentials handshake
  __call__(method, p)  -> send the JSON-RPC request frame; recv-until-matching-id (bounded by
                          request_timeout); return the parsed {"id","result"|"error"} dict
  close()              -> bounded ws.close() + loop.close(); swallow every error (teardown safe)

This mirrors the crypto sync-REST-on-the-main-thread order submit: submit/cancel block the GUI
thread for one bounded round-trip, exactly like the crypto signed REST POST. The 6b fill worker
keeps its OWN authed socket on its OWN QThread — two sockets, each one concern.

SYNC over async WITHOUT a thread: a dedicated persistent loop, run_until_complete per call. NOT
asyncio.run per call (which makes+tears a loop AND re-auths per order). NOT the fill worker's
off-thread socket (cross-thread coordination is a teardown surface).

TEARDOWN: DeribitExecutionClient.detach() calls close(); LiveOmsHub.shutdown() (live_oms.py:117)
calls getattr(client,'detach',None)() on the MAIN thread AFTER the fill worker is stop()+wait()-
joined, so the socket close is bounded and race-free (no background thread owns it). 0xC0000409-safe.

SECRETS: client_id/client_secret live only on this instance + the build_client_credentials_auth
frame (NEVER logged). Errors carry only {code,message}; no creds in events/exceptions/logs.
"""
from __future__ import annotations

import asyncio
import json
import time

from vike_trader_app.exec.deribit.rpc import JsonRpcBuilder, parse_response
from vike_trader_app.exec.deribit.ws_auth import build_client_credentials_auth
from vike_trader_app.exec.user_data_core import UserDataAuthError


def _default_now_ms() -> int:
    return int(time.monotonic() * 1000)


class DeribitOrderTransport:
    """Persistent authed JSON-RPC order WS. Drop-in for the 6a transport(method, params)->dict arg."""

    def __init__(self, *, ws_url: str, client_id: str, client_secret: str,
                 scope: str | None = None, builder: JsonRpcBuilder | None = None,
                 connect=None, request_timeout: float = 10.0, now_ms=None) -> None:
        self._ws_url = ws_url
        self._client_id = client_id          # SECRET — never logged
        self._client_secret = client_secret  # SECRET — never logged
        self._scope = scope
        self._builder = builder or JsonRpcBuilder()
        self._connect = connect              # async (ws_url) -> ws; None -> lazy websockets.connect
        self._request_timeout = float(request_timeout)
        self._now_ms = now_ms or _default_now_ms
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ws = None

    # --- lifecycle (MAIN thread) ------------------------------------------
    def connect(self) -> None:
        """Open ONE socket on a dedicated persistent loop and complete public/auth. MAIN thread."""
        if self._loop is not None:
            self.close()   # idempotent: a re-connect must not leak the prior loop+socket
        self._loop = asyncio.new_event_loop()
        self._ws = self._loop.run_until_complete(self._open_and_auth())

    async def _open_and_auth(self):
        if self._connect is None:
            import websockets  # noqa: PLC0415 — lazy: websockets is optional at import time
            ws = await websockets.connect(self._ws_url, open_timeout=10)
        else:
            ws = await self._connect(self._ws_url)
        try:
            auth_id = self._builder.next_id()
            frame = build_client_credentials_auth(
                client_id=self._client_id, client_secret=self._client_secret,
                scope=self._scope, rpc_id=auth_id)
            await ws.send(json.dumps(frame))   # NEVER log this frame (carries creds)
            _result, error = await self._recv_until_id(ws, auth_id)
            if error is not None:
                # NEVER include client_id/client_secret in the message.
                raise UserDataAuthError(f"Deribit order-WS auth failed: {error.get('message', '')}")
        except BaseException:
            await self._bounded_close(ws)
            raise
        return ws

    # --- the injected transport seam --------------------------------------
    def __call__(self, method: str, params: dict) -> dict:
        """Sync bounded JSON-RPC request/response by id. Returns the parsed {id,result|error} dict."""
        if self._loop is None or self._ws is None:
            raise RuntimeError("DeribitOrderTransport.connect() not called")
        return self._loop.run_until_complete(self._request(method, params))

    async def _request(self, method: str, params: dict) -> dict:
        rid = self._builder.next_id()
        frame = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        await self._ws.send(json.dumps(frame))
        result, error = await self._recv_until_id(self._ws, rid)
        # Hand back the SAME parsed-dict shape DeribitExecutionClient.parse_response expects.
        return {"id": rid, "result": result, "error": error}

    async def _recv_until_id(self, ws, rid: int):
        """Recv (bounded by request_timeout) until a JSON-RPC frame with id==rid arrives. Skip any
        interleaved frame (subscription notification / wrong id / non-JSON keepalive). Raise
        TimeoutError if the deadline passes (a stalled socket must NOT hang the GUI main thread)."""
        deadline = self._now_ms() + int(self._request_timeout * 1000)
        while True:
            remaining = (deadline - self._now_ms()) / 1000.0
            if remaining <= 0:
                raise TimeoutError("Deribit order-WS request timed out")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                continue  # non-JSON keepalive — skip
            fid, result, error = parse_response(parsed)
            if fid != rid:
                continue  # subscription notification / stale reply — keep looping
            return result, error

    def close(self) -> None:
        """Bounded close of the socket + loop. Idempotent; swallows every error (teardown safe)."""
        loop, ws = self._loop, self._ws
        self._loop, self._ws = None, None
        if loop is None:
            return
        try:
            if ws is not None and not loop.is_closed():
                loop.run_until_complete(self._bounded_close(ws))
        except Exception:  # noqa: BLE001 — close errors must not block teardown
            pass
        finally:
            try:
                loop.close()
            except Exception:  # noqa: BLE001
                pass

    async def _bounded_close(self, ws) -> None:
        """Abandon a slow close after request_timeout (user_data_core.py:68 pattern)."""
        try:
            await asyncio.wait_for(ws.close(), timeout=self._request_timeout)
        except Exception:  # noqa: BLE001 — close timeout/error must not propagate
            pass
