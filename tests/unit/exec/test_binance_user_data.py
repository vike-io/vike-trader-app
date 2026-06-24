"""Binance WS-API open_binance_user_data_ws + make_binance_run_core: TDD tests.

Covers:
- subscribe-then-ack success (correct signed subscribe request with method
  'userDataStream.subscribe.signature', apiKey/timestamp/signature in params)
- subscribe failure (UserDataAuthError; secret never leaked in message)
- Interleaved frames and non-JSON frames before the ack are tolerated
- Bounded teardown: stop() bail (test_handshake_bails_promptly_on_stop) + handshake_timeout
- End-to-end make_binance_run_core with a fake stream emitting FillEvent + OrderFilled
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.user_data_core import UserDataAuthError
from vike_trader_app.exec.binance.user_data import (
    _HandshakeStopped,
    make_binance_run_core,
    open_binance_user_data_ws,
)
from vike_trader_app.exec.events import FillEvent, OrderFilled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _AckEchoWS:
    """Echoes the sent request id with status 200 on the first recv."""
    def __init__(self): self.sent = []; self.closed = False; self._acked = False

    async def send(self, data): self.sent.append(data)

    async def recv(self):
        if not self._acked and self.sent:
            self._acked = True
            rid = json.loads(self.sent[0])["id"]
            return json.dumps({"id": rid, "status": 200, "result": {"subscriptionId": 0}})
        await asyncio.sleep(10); raise asyncio.TimeoutError

    async def close(self): self.closed = True


def _async_return(value):
    async def _c(url): return value
    return _c


def _run(coro, timeout=2.0): return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


# ---------------------------------------------------------------------------
# test_open_ws_subscribes_then_returns
# ---------------------------------------------------------------------------

def test_open_ws_subscribes_then_returns():
    """open_binance_user_data_ws sends a signed subscribe request and returns ws on status==200 ack."""
    ws = _AckEchoWS()

    result = _run(open_binance_user_data_ws(
        ws_url="wss://fake",
        api_key="MYAPIKEY",
        api_secret="MYSECRET",
        now_ms=lambda: 1_700_000_000_000,
        connect=_async_return(ws),
    ))

    assert result is ws

    # Exactly one frame must have been sent
    assert len(ws.sent) == 1, f"Expected 1 sent frame, got {len(ws.sent)}"

    frame = json.loads(ws.sent[0])
    assert frame["method"] == "userDataStream.subscribe.signature", (
        f"Expected method 'userDataStream.subscribe.signature', got {frame['method']!r}"
    )
    params = frame["params"]
    assert "apiKey" in params, "params must contain 'apiKey'"
    assert params["apiKey"] == "MYAPIKEY"
    assert "timestamp" in params, "params must contain 'timestamp'"
    assert "signature" in params, "params must contain 'signature'"


# ---------------------------------------------------------------------------
# test_subscribe_failure_raises_and_hides_secret
# ---------------------------------------------------------------------------

def test_subscribe_failure_raises_and_hides_secret():
    """status != 200 with error.msg raises UserDataAuthError; secret must NOT appear."""
    _SECRET = "SUPERSECRETKEY99"

    class _FailWS:
        def __init__(self): self.sent = []; self.closed = False

        async def send(self, data): self.sent.append(data)

        async def recv(self):
            rid = json.loads(self.sent[0])["id"]
            return json.dumps({
                "id": rid, "status": 400,
                "error": {"code": -2014, "msg": "API-key format invalid."},
            })

        async def close(self): self.closed = True

    ws = _FailWS()

    with pytest.raises(UserDataAuthError) as exc_info:
        _run(open_binance_user_data_ws(
            ws_url="wss://fake",
            api_key="MYAPIKEY",
            api_secret=_SECRET,
            now_ms=lambda: 0,
            connect=_async_return(ws),
        ))

    msg = str(exc_info.value)
    assert _SECRET not in msg, f"Secret leaked in UserDataAuthError: {msg}"
    # The signature (hex digest) also must not leak — but we can't easily check for the exact
    # hex here since we don't know it. The key thing is the raw secret string is absent.
    assert ws.closed is True, "ws must be closed on subscribe failure"


# ---------------------------------------------------------------------------
# test_interleaved_and_non_json_frames_before_ack_ignored
# ---------------------------------------------------------------------------

def test_interleaved_and_non_json_frames_before_ack_ignored():
    """Junk dict and raw 'ping' text before the id-matched ack must be silently ignored."""

    class _InterleavedWS:
        def __init__(self): self.sent = []; self.closed = False; self._step = 0

        async def send(self, data): self.sent.append(data)

        async def recv(self):
            rid = json.loads(self.sent[0])["id"] if self.sent else "x"
            self._step += 1
            if self._step == 1:
                return json.dumps({"someOtherKey": "noise"})
            if self._step == 2:
                return "ping"  # raw non-JSON
            # step 3: the real ack
            return json.dumps({"id": rid, "status": 200, "result": {"subscriptionId": 0}})

        async def close(self): self.closed = True

    ws = _InterleavedWS()

    result = _run(open_binance_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    assert result is ws, "Must succeed and return ws despite interleaved frames"


# ---------------------------------------------------------------------------
# _BlockingWS + bounded handshake tests
# ---------------------------------------------------------------------------

class _BlockingWS:
    """recv() blocks ~forever; close() flips closed=True. Models a half-open / stalled handshake."""

    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        await asyncio.sleep(3600)   # never returns within the test window
        raise AssertionError("unreachable")  # pragma: no cover

    async def close(self):
        self.closed = True


def test_handshake_bails_promptly_on_stop():
    """When stop() flips True mid-handshake, the coroutine raises _HandshakeStopped PROMPTLY
    (within 1s) and the ws is closed — this is the 0xC0000409 teardown fix."""
    ws = _BlockingWS()
    flag = {"v": False}

    async def _drive():
        task = asyncio.ensure_future(open_binance_user_data_ws(
            ws_url="wss://fake",
            api_key="K",
            api_secret="S",
            now_ms=lambda: 0,
            connect=_async_return(ws),
            stop=lambda: flag["v"],
            recv_timeout=0.05,
            handshake_timeout=999,
        ))
        await asyncio.sleep(0.1)
        flag["v"] = True   # request stop while recv() is blocked forever
        with pytest.raises(_HandshakeStopped):
            await task

    # Whole drive must finish well within 1s despite recv() awaiting 3600s.
    asyncio.run(asyncio.wait_for(_drive(), timeout=1.0))
    assert ws.closed is True, "ws.close() must be called on a stop-during-handshake"


def test_handshake_deadline_raises_auth_error():
    """A persistent ack stall (stop stays False) hits handshake_timeout -> UserDataAuthError;
    the ws is closed and no secret/api_key leaks into str(exc)."""
    import time

    _SECRET = "SUPERSECRETKEY99"
    ws = _BlockingWS()
    _now = lambda: int(time.monotonic() * 1000)   # real advancing clock so the deadline can pass

    async def _drive():
        with pytest.raises(UserDataAuthError) as exc_info:
            await open_binance_user_data_ws(
                ws_url="wss://fake",
                api_key="MYKEY123",
                api_secret=_SECRET,
                now_ms=_now,
                connect=_async_return(ws),
                stop=lambda: False,
                recv_timeout=0.05,
                handshake_timeout=0.1,
            )
        return exc_info.value

    exc = asyncio.run(asyncio.wait_for(_drive(), timeout=1.0))
    assert ws.closed is True, "ws.close() must be called on a handshake deadline"
    assert _SECRET not in str(exc)
    assert "MYKEY123" not in str(exc)


# ---------------------------------------------------------------------------
# test_make_run_core_end_to_end_with_fake_stream
# ---------------------------------------------------------------------------

class _StreamWS:
    """WS that echoes the subscribe ack then emits one wrapped executionReport, then goes idle."""

    def __init__(self):
        self._frames: list[str] = []
        self._sent: list[str] = []
        self._ready = False
        self.closed = False
        self.sent = self._sent

    async def send(self, data):
        self._sent.append(data)
        if not self._ready:
            # Build the ack echoing the request id we just received
            rid = json.loads(data)["id"]
            self._frames.append(
                json.dumps({"id": rid, "status": 200, "result": {"subscriptionId": 0}})
            )
            # Queue the stream payload
            self._frames.append(json.dumps({
                "subscriptionId": 0,
                "event": {
                    "e": "executionReport",
                    "s": "BTCUSDT",
                    "c": "clientOrd1",
                    "S": "BUY",
                    "x": "TRADE",
                    "X": "FILLED",
                    "i": 12345,
                    "l": "0.001",
                    "L": "50000",
                    "n": "0.05",
                    "m": False,
                    "t": "T1",
                    "T": 1700000000000,
                },
            }))
            self._ready = True

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(10)
        raise asyncio.TimeoutError

    async def close(self):
        self.closed = True


def test_make_run_core_end_to_end_with_fake_stream():
    """make_binance_run_core emits a FillEvent + OrderFilled from an executionReport frame
    and closes the ws on stop."""
    ws = _StreamWS()
    seen = []
    stop_flag = {"v": False}

    def _stop():
        # Stop after we've seen at least one FillEvent
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_binance_run_core(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        symbol="BTCUSDT",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    )

    # run_core is synchronous (calls asyncio.run internally)
    run_core(seen.append, _stop)

    # Must have emitted a FillEvent with trade_id=='T1' and symbol=='BTCUSDT'
    fill_events = [e for e in seen if isinstance(e, FillEvent)]
    assert fill_events, f"Expected at least one FillEvent, got: {[type(e).__name__ for e in seen]}"
    assert fill_events[0].trade_id == "T1"
    assert fill_events[0].symbol == "BTCUSDT"

    # Must ALSO have emitted an OrderFilled whose .fill.trade_id=='T1'
    assert any(isinstance(e, OrderFilled) and e.fill.trade_id == "T1" for e in seen), (
        f"Expected an OrderFilled wrapping the T1 fill; seen types: {[type(e).__name__ for e in seen]}"
    )

    # WS must be closed after stop
    assert ws.closed is True, "ws.close() must be called when run_core exits"
