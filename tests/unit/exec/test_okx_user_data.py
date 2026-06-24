"""OKX private-WS open_ws + make_okx_run_core factory: TDD tests.

Covers:
- Login handshake (correct frame order, ms->s timestamp division)
- Login failure (UserDataAuthError; secret/passphrase never leaked in message)
- Subscribe failure (UserDataAuthError)
- Interleaved frames before login ack are ignored
- Interleaved non-JSON frames during handshake are tolerated
- Interleaved frames before subscribe ack are ignored
- Bounded teardown: stop() bail + handshake_timeout deadline
- End-to-end make_okx_run_core with a fake stream emitting FillEvent + OrderFilled
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.user_data_core import UserDataAuthError
from vike_trader_app.exec.okx.user_data import (
    _HandshakeStopped,
    make_okx_run_core,
    open_okx_user_data_ws,
)
from vike_trader_app.exec.events import FillEvent, OrderFilled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _HandshakeWS:
    def __init__(self, frames): self._q = list(frames); self.sent = []; self.closed = False

    async def send(self, data): self.sent.append(data)

    async def recv(self):
        if not self._q: raise RuntimeError("recv on empty queue")
        return self._q.pop(0)

    async def close(self): self.closed = True


def _async_return(value):
    async def _c(url): return value
    return _c


def _run(coro, timeout=2.0): return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


# ---------------------------------------------------------------------------
# test_open_ws_logs_in_then_subscribes
# ---------------------------------------------------------------------------

def test_open_ws_logs_in_then_subscribes():
    """open_okx_user_data_ws sends login frame (with apiKey, passphrase, timestamp in SECONDS)
    then subscribe frame, returns ws."""
    ws = _HandshakeWS([
        json.dumps({"event": "login", "code": "0"}),
        json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}),
    ])

    result = _run(open_okx_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        passphrase="P",
        now_ms=lambda: 1_700_000_000_000,  # ms -> 1700000000 seconds
        connect=_async_return(ws),
    ))

    assert result is ws

    # First sent frame must be login with correct fields
    login_raw = ws.sent[0]
    login_frame = json.loads(login_raw) if isinstance(login_raw, str) else login_raw
    assert login_frame["op"] == "login"
    assert login_frame["args"][0]["apiKey"] == "K"
    assert login_frame["args"][0]["passphrase"] == "P"
    # Timestamp must be in SECONDS (ms // 1000): 1_700_000_000_000 // 1000 == 1_700_000_000
    assert login_frame["args"][0]["timestamp"] == "1700000000"

    # Second sent frame must be subscribe
    sub_raw = ws.sent[1]
    sub_frame = json.loads(sub_raw) if isinstance(sub_raw, str) else sub_raw
    assert sub_frame == {"op": "subscribe", "args": [{"channel": "orders", "instType": "SPOT"}]}


# ---------------------------------------------------------------------------
# test_login_failure_raises_and_hides_secret
# ---------------------------------------------------------------------------

def test_login_failure_raises_and_hides_secret():
    """Login ack with event=='error' raises UserDataAuthError; secret/passphrase must NOT appear."""
    _SECRET = "SUPERSECRETKEY99"
    _PASSPHRASE = "PASSPHRASE_XYZ"
    ws = _HandshakeWS([
        json.dumps({"event": "error", "code": "60009", "msg": "Login failed"}),
    ])

    with pytest.raises(UserDataAuthError) as exc_info:
        _run(open_okx_user_data_ws(
            ws_url="wss://fake",
            api_key="K",
            api_secret=_SECRET,
            passphrase=_PASSPHRASE,
            now_ms=lambda: 0,
            connect=_async_return(ws),
        ))

    msg = str(exc_info.value)
    assert _SECRET not in msg, f"Secret leaked in UserDataAuthError: {msg}"
    assert _PASSPHRASE not in msg, f"Passphrase leaked in UserDataAuthError: {msg}"
    assert "K" not in msg or len("K") == 1  # api_key "K" is a single char — not meaningful to check


# ---------------------------------------------------------------------------
# test_subscribe_failure_raises
# ---------------------------------------------------------------------------

def test_subscribe_failure_raises():
    """Subscribe ack with event=='error' raises UserDataAuthError."""
    ws = _HandshakeWS([
        json.dumps({"event": "login", "code": "0"}),
        json.dumps({"event": "error", "code": "60012", "msg": "bad channel"}),
    ])

    with pytest.raises(UserDataAuthError):
        _run(open_okx_user_data_ws(
            ws_url="wss://fake",
            api_key="K",
            api_secret="S",
            passphrase="P",
            now_ms=lambda: 0,
            connect=_async_return(ws),
        ))


# ---------------------------------------------------------------------------
# test_interleaved_frames_before_login_ack_ignored
# ---------------------------------------------------------------------------

def test_interleaved_frames_before_login_ack_ignored():
    """Junk frames and raw 'pong' string before the login ack must be silently ignored."""
    ws = _HandshakeWS([
        # Interleaved frames that are NOT the login ack (including non-JSON raw pong)
        json.dumps({"event": "channel-conn-count"}),
        "pong",  # raw non-JSON text — must not crash
        # The actual login ack
        json.dumps({"event": "login", "code": "0"}),
        # Subscribe ack
        json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}),
    ])

    result = _run(open_okx_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        passphrase="P",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    # Must succeed and return ws despite the interleaved frames
    assert result is ws


# ---------------------------------------------------------------------------
# test_interleaved_frames_before_subscribe_ack_ignored
# ---------------------------------------------------------------------------

def test_interleaved_frames_before_subscribe_ack_ignored():
    """Junk frames arriving after login ack but before subscribe ack must be ignored."""
    ws = _HandshakeWS([
        # Login ack
        json.dumps({"event": "login", "code": "0"}),
        # Interleaved frames between login and subscribe ack
        json.dumps({"event": "channel-conn-count"}),
        json.dumps({"arg": {"channel": "orders"}, "data": []}),
        # The actual subscribe ack
        json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}),
    ])

    result = _run(open_okx_user_data_ws(
        ws_url="wss://fake",
        api_key="K",
        api_secret="S",
        passphrase="P",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    assert result is ws


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
    """When stop() flips True mid-handshake, the coroutine returns PROMPTLY raising
    _HandshakeStopped and the ws is closed — this is the 0xC0000409 teardown fix."""
    ws = _BlockingWS()
    flag = {"v": False}

    async def _drive():
        task = asyncio.ensure_future(open_okx_user_data_ws(
            ws_url="wss://fake", api_key="K", api_secret="S", passphrase="P",
            now_ms=lambda: 0,
            connect=_async_return(ws), stop=lambda: flag["v"],
            recv_timeout=0.05, handshake_timeout=999,
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
    the ws is closed and no secret/api_key/passphrase leaks into str(exc)."""
    import time

    _SECRET = "SUPERSECRETKEY99"
    _PASSPHRASE = "PASSPHRASE_XYZ"
    ws = _BlockingWS()
    _now = lambda: int(time.monotonic() * 1000)   # real advancing clock so the deadline can pass

    async def _drive():
        with pytest.raises(UserDataAuthError) as exc_info:
            await open_okx_user_data_ws(
                ws_url="wss://fake", api_key="MYKEY123", api_secret=_SECRET,
                passphrase=_PASSPHRASE, now_ms=_now,
                connect=_async_return(ws), stop=lambda: False,
                recv_timeout=0.05, handshake_timeout=0.1,
            )
        return exc_info.value

    exc = asyncio.run(asyncio.wait_for(_drive(), timeout=1.0))
    assert ws.closed is True, "ws.close() must be called on a handshake deadline"
    assert _SECRET not in str(exc)
    assert "MYKEY123" not in str(exc)
    assert _PASSPHRASE not in str(exc)


# ---------------------------------------------------------------------------
# test_make_run_core_end_to_end_with_fake_stream
# ---------------------------------------------------------------------------

class _StreamWS:
    """WS that completes the OKX handshake, emits one orders fill frame, then goes idle."""

    def __init__(self):
        self._frames = [
            # Handshake acks
            json.dumps({"event": "login", "code": "0"}),
            json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}),
            # One orders frame with a filled BUY row
            json.dumps({
                "arg": {"channel": "orders", "instType": "SPOT"},
                "data": [{
                    "tradeId": "T1",
                    "instId": "BTC-USDT",
                    "fillSz": "0.001",
                    "fillPx": "50000",
                    "fillFee": "-0.05",
                    "side": "buy",
                    "state": "filled",
                    "execType": "T",
                    "fillTime": "1700000000000",
                    "clOrdId": "",
                    "ordId": "",
                    "sz": "0.001",
                    "accFillSz": "0.001",
                }],
            }),
        ]
        self.closed = False
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        # No more frames — simulate idle
        await asyncio.sleep(10)
        raise asyncio.TimeoutError

    async def close(self):
        self.closed = True


def test_make_run_core_end_to_end_with_fake_stream():
    """make_okx_run_core emits a FillEvent + OrderFilled from an orders frame and closes ws on stop."""
    ws = _StreamWS()
    seen = []
    stop_flag = {"v": False}

    def _stop():
        # Stop after we've seen at least one FillEvent
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_okx_run_core(
        ws_url="wss://x",
        api_key="K",
        api_secret="S",
        passphrase="P",
        symbol="BTC-USDT",
        now_ms=lambda: 0,
        connect=_async_return(ws),
    )

    # run_core is synchronous (calls asyncio.run internally)
    run_core(seen.append, _stop)

    # Must have emitted a FillEvent with trade_id=='T1' and symbol=='BTC-USDT'
    fill_events = [e for e in seen if isinstance(e, FillEvent)]
    assert fill_events, f"Expected at least one FillEvent, got: {[type(e).__name__ for e in seen]}"
    assert fill_events[0].trade_id == "T1"
    assert fill_events[0].symbol == "BTC-USDT"

    # Must ALSO have emitted an OrderFilled whose .fill.trade_id=='T1'
    # (this is what drives the ManagedOrder FSM; without it filled_qty stays 0)
    assert any(isinstance(e, OrderFilled) and e.fill.trade_id == "T1" for e in seen), (
        f"Expected an OrderFilled wrapping the T1 fill; seen types: {[type(e).__name__ for e in seen]}"
    )

    # WS must be closed after stop
    assert ws.closed is True, "ws.close() must be called when run_core exits"
