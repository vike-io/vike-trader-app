"""run_user_data_forever async core: mints listenKey, maps + emits frames, raises on auth, joins idle."""

import asyncio
import json

import pytest

from vike_trader_app.exec.binance.user_data import (
    UserDataAuthError,
    run_user_data_forever,
)
from vike_trader_app.exec.events import FillEvent, OrderAccepted


class _FakeWS:
    def __init__(self, frames, stop_after=True):
        self._frames = list(frames)
        self.closed = False

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(0.01)
        raise TimeoutError  # idle -> recv timeout path

    async def close(self):
        self.closed = True


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=2.0))


def test_emits_mapped_events_then_stops():
    frames = [
        json.dumps({"e": "executionReport", "s": "BTCUSDT", "c": "s-0", "S": "BUY",
                    "x": "NEW", "X": "NEW", "i": 5, "T": 1}),
        json.dumps({"e": "executionReport", "s": "BTCUSDT", "c": "s-0", "S": "BUY",
                    "x": "TRADE", "X": "FILLED", "i": 5, "l": "1", "L": "100", "n": "0",
                    "m": True, "t": 9, "T": 2}),
    ]
    seen = []
    ws = _FakeWS(frames)
    stop_flag = {"v": False}

    def _stop():
        # stop once both frames are drained
        if not ws._frames:
            stop_flag["v"] = True
        return stop_flag["v"]

    _run(run_user_data_forever(
        seen.append, venue="binance", symbol="BTCUSDT",
        mint_listen_key=lambda: "LK", keepalive=lambda k: None,
        open_ws=lambda url: _async_return(ws), stop=_stop, now_ms=lambda: 0,
        recv_timeout=0.05))
    assert any(isinstance(e, OrderAccepted) for e in seen)
    assert any(isinstance(e, FillEvent) for e in seen)
    assert ws.closed   # socket closed in finally


def test_auth_error_raises_not_loops():
    def _mint():
        raise UserDataAuthError("bad key")

    with pytest.raises(UserDataAuthError):
        _run(run_user_data_forever(
            lambda e: None, venue="binance", symbol="BTCUSDT",
            mint_listen_key=_mint, keepalive=lambda k: None,
            open_ws=lambda url: _async_return(_FakeWS([])), stop=lambda: False,
            now_ms=lambda: 0))


async def _async_return(value):
    return value
