"""Bybit private-WS open_ws + make_bybit_run_core factory: TDD tests.

Covers:
- Auth handshake (correct frame order)
- Auth failure (UserDataAuthError; secret never leaked in message)
- Subscribe failure (UserDataAuthError)
- Interleaved frames before auth ack are ignored (LOOP until op==auth)
- Interleaved frames before subscribe ack are ignored (LOOP until op==subscribe)
- End-to-end make_bybit_run_core with a fake stream emitting a FillEvent
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.user_data_core import UserDataAuthError
from vike_trader_app.exec.bybit.user_data import (
    make_bybit_run_core,
    open_bybit_user_data_ws,
)
from vike_trader_app.exec.events import FillEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _HandshakeWS:
    """Scripted recv queue + sent-frame capture for handshake tests."""

    def __init__(self, frames: list[str]):
        self._queue = list(frames)
        self.sent: list[dict] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if not self._queue:
            raise RuntimeError("_HandshakeWS: recv() called but queue is empty")
        return self._queue.pop(0)

    async def close(self) -> None:
        self.closed = True


def _async_return(value):
    """Return an async callable that resolves to *value* (for the connect= parameter)."""
    async def _connect(url):  # noqa: ARG001
        return value
    return _connect


def _run(coro, timeout: float = 2.0):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


# ---------------------------------------------------------------------------
# test_open_ws_auths_then_subscribes
# ---------------------------------------------------------------------------

def test_open_ws_auths_then_subscribes():
    """open_bybit_user_data_ws sends auth frame (with api_key) then subscribe frame, returns ws."""
    ws = _HandshakeWS([
        json.dumps({"op": "auth", "success": True}),
        json.dumps({"op": "subscribe", "success": True}),
    ])

    result = _run(open_bybit_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    # Should return the ws object
    assert result is ws

    # First sent frame must be auth with api_key
    assert len(ws.sent) >= 2, f"Expected at least 2 sent frames, got {ws.sent}"
    assert ws.sent[0]["op"] == "auth"
    assert ws.sent[0]["args"][0] == "K"

    # Second sent frame must be subscribe
    assert ws.sent[1] == {"op": "subscribe", "args": ["execution", "order"]}


# ---------------------------------------------------------------------------
# test_open_ws_raises_on_auth_failure
# ---------------------------------------------------------------------------

def test_open_ws_raises_on_auth_failure():
    """Auth ack with success=False raises UserDataAuthError; secret must NOT appear in the message."""
    _SECRET = "SUPERSECRETKEY99"  # long enough to not appear accidentally in error text
    ws = _HandshakeWS([
        json.dumps({"op": "auth", "success": False, "ret_msg": "invalid signature"}),
    ])

    with pytest.raises(UserDataAuthError) as exc_info:
        _run(open_bybit_user_data_ws(
            ws_url="wss://fake",
            api_key="K",
            api_secret=_SECRET,
            now_ms=lambda: 0,
            connect=_async_return(ws),
        ))

    # Secret must NEVER appear in the error message (secrets-never-logged guard)
    assert _SECRET not in str(exc_info.value), (
        f"Secret leaked in UserDataAuthError message: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# test_open_ws_raises_on_subscribe_failure
# ---------------------------------------------------------------------------

def test_open_ws_raises_on_subscribe_failure():
    """Subscribe ack with success=False raises UserDataAuthError."""
    ws = _HandshakeWS([
        json.dumps({"op": "auth", "success": True}),
        json.dumps({"op": "subscribe", "success": False, "ret_msg": "bad topic"}),
    ])

    with pytest.raises(UserDataAuthError):
        _run(open_bybit_user_data_ws(
            ws_url="wss://fake",
            api_key="K",
            api_secret="S",
            now_ms=lambda: 0,
            connect=_async_return(ws),
        ))


# ---------------------------------------------------------------------------
# test_open_ws_ignores_interleaved_frames_before_auth_ack
# ---------------------------------------------------------------------------

def test_open_ws_ignores_interleaved_frames_before_auth_ack():
    """Junk frames arriving before the auth ack must be silently ignored (LOOP recv until op==auth)."""
    ws = _HandshakeWS([
        # Interleaved frames that are NOT the auth ack
        json.dumps({"op": "pong", "req_id": "ping_1"}),
        json.dumps({"topic": "heartbeat", "data": {}}),
        json.dumps({"some_random": "unsolicited_push"}),
        # The actual auth ack
        json.dumps({"op": "auth", "success": True}),
        # Subscribe ack
        json.dumps({"op": "subscribe", "success": True}),
    ])

    result = _run(open_bybit_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    # Must succeed and return ws despite the interleaved frames
    assert result is ws


# ---------------------------------------------------------------------------
# test_open_ws_ignores_interleaved_frames_before_subscribe_ack
# ---------------------------------------------------------------------------

def test_open_ws_ignores_interleaved_frames_before_subscribe_ack():
    """Junk frames arriving after auth ack but before subscribe ack must be ignored."""
    ws = _HandshakeWS([
        # Auth ack arrives cleanly
        json.dumps({"op": "auth", "success": True}),
        # Interleaved frames between auth ack and subscribe ack
        json.dumps({"op": "pong", "req_id": "ping_0"}),
        json.dumps({"topic": "tickers", "data": []}),
        # The actual subscribe ack
        json.dumps({"op": "subscribe", "success": True}),
    ])

    result = _run(open_bybit_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    assert result is ws


# ---------------------------------------------------------------------------
# test_make_run_core_end_to_end_with_fake_stream
# ---------------------------------------------------------------------------

class _StreamWS:
    """WS that completes the handshake, then emits one execution frame, then times out."""

    def __init__(self):
        self._frames = [
            # Handshake acks
            json.dumps({"op": "auth", "success": True}),
            json.dumps({"op": "subscribe", "success": True}),
            # One execution frame with a Trade row
            json.dumps({
                "topic": "execution",
                "data": [{
                    "execType": "Trade",
                    "execId": "EXEC001",
                    "orderLinkId": "coid-1",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "execQty": "0.001",
                    "execPrice": "50000",
                    "execFee": "0.05",
                    "isMaker": False,
                    "execTime": "1700000000000",
                    "cumExecQty": "0.001",
                    "orderQty": "0.001",
                    "leavesQty": "0.0",
                }],
            }),
        ]
        self.closed = False
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        # No more frames — simulate idle (triggers asyncio.TimeoutError in run_user_data_forever)
        await asyncio.sleep(10)
        raise asyncio.TimeoutError

    async def close(self) -> None:
        self.closed = True


def test_make_run_core_end_to_end_with_fake_stream():
    """make_bybit_run_core emits a FillEvent from an execution frame and closes the ws on stop."""
    ws = _StreamWS()
    seen = []
    stop_flag = {"v": False}

    def _stop():
        # Stop after we've seen at least one event
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_bybit_run_core(
        ws_url="wss://x",
        api_key="K",
        api_secret="S",
        symbol="BTCUSDT",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    )

    # run_core is synchronous (calls asyncio.run internally)
    run_core(seen.append, _stop)

    # Must have emitted at least one FillEvent
    fill_events = [e for e in seen if isinstance(e, FillEvent)]
    assert fill_events, f"Expected at least one FillEvent, got: {[type(e).__name__ for e in seen]}"
    assert fill_events[0].trade_id == "EXEC001"
    assert fill_events[0].symbol == "BTCUSDT"

    # WS must be closed after stop
    assert ws.closed is True, "ws.close() must be called when run_core exits"
