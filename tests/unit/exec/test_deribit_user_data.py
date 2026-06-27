"""Deribit private-WS open_ws + make_deribit_run_core factory: TDD with scripted JSON-RPC frames.

Covers:
- Auth (public/auth client_credentials) then subscribe (private/subscribe) handshake order + ids
- Auth result captured into the token cell (access_token + refresh_token)
- Auth failure (error dict) -> UserDataAuthError; client_secret/access_token never leaked
- Subscribe failure -> UserDataAuthError
- Interleaved subscription notification before the auth ack is ignored
- Interleaved non-JSON keepalive during handshake is tolerated
- Bounded teardown: stop() bail (_HandshakeStopped) + handshake_timeout deadline (UserDataAuthError)
- End-to-end make_deribit_run_core emits FillEvent + OrderFilled from a user.trades frame
- The ping hook sends a public/auth grant_type=refresh_token frame using the captured refresh_token
- Channel construction: open_deribit_user_data_ws directly proves the subscribe frame's channel
  (CRITIC FIX 2: do NOT use stop=lambda:True through make_run_core — bails before open_ws).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.user_data_core import UserDataAuthError
from vike_trader_app.exec.deribit.user_data import (
    _HandshakeStopped,
    _refresh_token,
    make_deribit_run_core,
    open_deribit_user_data_ws,
)
from vike_trader_app.exec.events import FillEvent, OrderFilled


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _HandshakeWS:
    def __init__(self, frames):
        self._q = list(frames); self.sent = []; self.closed = False

    async def send(self, data): self.sent.append(data)

    async def recv(self):
        if not self._q:
            raise RuntimeError("recv on empty queue")
        return self._q.pop(0)

    async def close(self): self.closed = True


def _async_return(value):
    async def _c(url): return value
    return _c


def _run(coro, timeout=2.0):
    return asyncio.run(asyncio.wait_for(coro, timeout=timeout))


def _auth_result(rid, *, access="AT", refresh="RT"):
    return json.dumps({"jsonrpc": "2.0", "id": rid,
                       "result": {"access_token": access, "refresh_token": refresh,
                                  "expires_in": 31536000, "token_type": "bearer"}})


def _sub_result(rid, channels):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "result": list(channels)})


# ---------------------------------------------------------------------------
# Handshake order + token capture
# ---------------------------------------------------------------------------

def test_open_ws_auths_then_subscribes_and_captures_tokens():
    """open_deribit_user_data_ws sends public/auth (id=1) then private/subscribe (id=2),
    returns ws, and captures access_token + refresh_token into token_cell."""
    ws = _HandshakeWS([_auth_result(1), _sub_result(2, ["user.trades.option.BTC.raw"])])
    cell: dict = {}

    result = _run(open_deribit_user_data_ws(
        ws_url="wss://fake", client_id="cid", client_secret="csec",
        channels=["user.trades.option.BTC.raw"], now_ms=lambda: 0,
        token_cell=cell, connect=_async_return(ws),
    ))
    assert result is ws

    auth_frame = json.loads(ws.sent[0])
    assert auth_frame["method"] == "public/auth"
    assert auth_frame["params"]["grant_type"] == "client_credentials"
    assert auth_frame["params"]["client_id"] == "cid"
    assert auth_frame["id"] == 1

    sub_frame = json.loads(ws.sent[1])
    assert sub_frame["method"] == "private/subscribe"
    assert sub_frame["params"]["channels"] == ["user.trades.option.BTC.raw"]
    assert sub_frame["id"] == 2

    assert cell["access_token"] == "AT"
    assert cell["refresh_token"] == "RT"


def test_auth_failure_raises_and_hides_secret():
    """An error result on the auth id raises UserDataAuthError; client_secret/access_token absent."""
    _SECRET = "SUPERSECRETKEY99"
    ws = _HandshakeWS([
        json.dumps({"jsonrpc": "2.0", "id": 1,
                    "error": {"code": 13004, "message": "invalid_credentials"}}),
    ])
    with pytest.raises(UserDataAuthError) as exc_info:
        _run(open_deribit_user_data_ws(
            ws_url="wss://fake", client_id="cid", client_secret=_SECRET,
            channels=["user.trades.option.BTC.raw"], now_ms=lambda: 0,
            connect=_async_return(ws),
        ))
    msg = str(exc_info.value)
    assert _SECRET not in msg
    assert "access_token" not in msg or "AT" not in msg
    assert ws.closed is True


def test_subscribe_failure_raises():
    """An error result on the subscribe id raises UserDataAuthError."""
    ws = _HandshakeWS([
        _auth_result(1),
        json.dumps({"jsonrpc": "2.0", "id": 2,
                    "error": {"code": 11050, "message": "bad_request"}}),
    ])
    with pytest.raises(UserDataAuthError):
        _run(open_deribit_user_data_ws(
            ws_url="wss://fake", client_id="cid", client_secret="csec",
            channels=["user.trades.option.BTC.raw"], now_ms=lambda: 0,
            connect=_async_return(ws),
        ))


def test_interleaved_notification_before_auth_ack_ignored():
    """A subscription notification + raw keepalive arriving before the auth result are ignored."""
    ws = _HandshakeWS([
        json.dumps({"jsonrpc": "2.0", "method": "subscription",
                    "params": {"channel": "user.trades.option.BTC.raw", "data": []}}),
        "pong",  # raw non-JSON — must not crash
        _auth_result(1),
        _sub_result(2, ["user.trades.option.BTC.raw"]),
    ])
    result = _run(open_deribit_user_data_ws(
        ws_url="wss://fake", client_id="cid", client_secret="csec",
        channels=["user.trades.option.BTC.raw"], now_ms=lambda: 0,
        connect=_async_return(ws),
    ))
    assert result is ws


# ---------------------------------------------------------------------------
# Bounded teardown
# ---------------------------------------------------------------------------

class _BlockingWS:
    """recv() blocks ~forever; close() flips closed=True. Models a half-open / stalled handshake."""

    def __init__(self):
        self.sent = []; self.closed = False

    async def send(self, data): self.sent.append(data)

    async def recv(self):
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")  # pragma: no cover

    async def close(self): self.closed = True


def test_handshake_bails_promptly_on_stop():
    """stop() flipping True mid-handshake returns PROMPTLY raising _HandshakeStopped + closes ws."""
    ws = _BlockingWS()
    flag = {"v": False}

    async def _drive():
        task = asyncio.ensure_future(open_deribit_user_data_ws(
            ws_url="wss://fake", client_id="cid", client_secret="csec",
            channels=["user.trades.option.BTC.raw"], now_ms=lambda: 0,
            connect=_async_return(ws), stop=lambda: flag["v"],
            recv_timeout=0.05, handshake_timeout=999,
        ))
        await asyncio.sleep(0.1)
        flag["v"] = True
        with pytest.raises(_HandshakeStopped):
            await task

    asyncio.run(asyncio.wait_for(_drive(), timeout=1.0))
    assert ws.closed is True


def test_handshake_deadline_raises_auth_error():
    """A persistent ack stall hits handshake_timeout -> UserDataAuthError; no secret leaks."""
    import time

    _SECRET = "SUPERSECRETKEY99"
    ws = _BlockingWS()
    _now = lambda: int(time.monotonic() * 1000)

    async def _drive():
        with pytest.raises(UserDataAuthError) as exc_info:
            await open_deribit_user_data_ws(
                ws_url="wss://fake", client_id="MYKEY123", client_secret=_SECRET,
                channels=["user.trades.option.BTC.raw"], now_ms=_now,
                connect=_async_return(ws), stop=lambda: False,
                recv_timeout=0.05, handshake_timeout=0.1,
            )
        return exc_info.value

    exc = asyncio.run(asyncio.wait_for(_drive(), timeout=1.0))
    assert ws.closed is True
    assert _SECRET not in str(exc)
    assert "MYKEY123" not in str(exc)


# ---------------------------------------------------------------------------
# Token-refresh ping hook
# ---------------------------------------------------------------------------

def test_refresh_token_ping_sends_refresh_frame():
    """_refresh_token sends a public/auth grant_type=refresh_token frame using the cell's token."""
    from vike_trader_app.exec.deribit.rpc import JsonRpcBuilder

    ws = _HandshakeWS([])  # send-only
    cell = {"access_token": "AT", "refresh_token": "RT-CURRENT"}
    ping = _refresh_token(builder=JsonRpcBuilder(start=99), token_cell=cell)

    _run(ping(ws))

    assert len(ws.sent) == 1
    frame = json.loads(ws.sent[0])
    assert frame["method"] == "public/auth"
    assert frame["params"]["grant_type"] == "refresh_token"
    assert frame["params"]["refresh_token"] == "RT-CURRENT"
    assert frame["id"] == 99


def test_refresh_token_ping_swallows_errors():
    """A send failure inside the ping hook must NOT propagate (it would thrash the connection)."""
    from vike_trader_app.exec.deribit.rpc import JsonRpcBuilder

    class _BoomWS:
        async def send(self, data): raise RuntimeError("socket gone")

    cell = {"access_token": "AT", "refresh_token": "RT"}
    ping = _refresh_token(builder=JsonRpcBuilder(), token_cell=cell)
    _run(ping(_BoomWS()))  # must not raise


# ---------------------------------------------------------------------------
# End-to-end make_deribit_run_core
# ---------------------------------------------------------------------------

class _StreamWS:
    """WS that completes the handshake, emits one user.trades fill frame, then goes idle."""

    def __init__(self):
        self._frames = [
            _auth_result(1),
            _sub_result(2, ["user.trades.option.BTC.raw"]),
            json.dumps({
                "jsonrpc": "2.0", "method": "subscription",
                "params": {
                    "channel": "user.trades.option.BTC.raw",
                    "data": [{
                        "trade_id": "T1",
                        "order_id": "O1",
                        "instrument_name": "BTC-25SEP20-9000-C",
                        "direction": "buy",
                        "amount": 1.5,
                        "price": 0.025,
                        "fee": 0.0005,
                        "liquidity": "T",
                        "state": "filled",
                        "timestamp": 1590484255886,
                        "label": "",
                    }],
                },
            }),
        ]
        self.closed = False; self.sent = []

    async def send(self, data): self.sent.append(data)

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(10)
        raise asyncio.TimeoutError

    async def close(self): self.closed = True


def test_make_run_core_end_to_end_with_fake_stream():
    """make_deribit_run_core emits FillEvent + OrderFilled from a user.trades frame, closes ws on stop."""
    ws = _StreamWS()
    seen = []
    stop_flag = {"v": False}

    def _stop():
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_deribit_run_core(
        ws_url="wss://x", client_id="cid", client_secret="csec",
        symbol="BTC-25SEP20-9000-C", currency="BTC",
        now_ms=lambda: 0, connect=_async_return(ws),
    )
    run_core(seen.append, _stop)

    fills = [e for e in seen if isinstance(e, FillEvent)]
    assert fills and fills[0].trade_id == "T1"
    assert fills[0].symbol == "BTC-25SEP20-9000-C"
    assert any(isinstance(e, OrderFilled) and e.fill.trade_id == "T1" for e in seen)
    assert ws.closed is True


def test_make_run_core_subscribes_to_user_trades_channel():
    """The factory builds the user.trades.{kind}.{currency}.{interval} channel.

    CRITIC FIX 2: test open_deribit_user_data_ws DIRECTLY (not through make_run_core
    with stop=lambda:True, which bails the outer while-not-stop loop before open_ws runs).
    Assert ws.sent[1] is the private/subscribe with the right channel.
    """
    ws = _HandshakeWS([_auth_result(1), _sub_result(2, ["user.trades.option.ETH.raw"])])

    _run(open_deribit_user_data_ws(
        ws_url="wss://x", client_id="cid", client_secret="csec",
        channels=["user.trades.option.ETH.raw"], now_ms=lambda: 0,
        connect=_async_return(ws),
    ))

    assert len(ws.sent) == 2
    sub_frame = json.loads(ws.sent[1])
    assert sub_frame["method"] == "private/subscribe"
    assert sub_frame["params"]["channels"] == ["user.trades.option.ETH.raw"]
