"""make_bybit_perp_run_core: TDD tests.

Covers:
- End-to-end make_bybit_perp_run_core with a fake stream emitting a linear execution frame
- FillEvent.mark_price is set (proving map_bybit_perp is wired, not map_bybit_private)
- WS is closed after stop
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.bybit.perp_user_data import make_bybit_perp_run_core
from vike_trader_app.exec.events import FillEvent


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_bybit_user_data.py)
# ---------------------------------------------------------------------------


def _async_return(value):
    """Return an async callable that resolves to *value* (for the connect= parameter)."""
    async def _connect(url):  # noqa: ARG001
        return value
    return _connect


class _PerpStreamWS:
    """WS that completes the handshake, then emits one linear execution frame with markPrice."""

    def __init__(self):
        self._frames = [
            # Handshake acks
            json.dumps({"op": "auth", "success": True}),
            json.dumps({"op": "subscribe", "success": True}),
            # One linear execution frame — carries markPrice (the perp-decoder proof)
            json.dumps({
                "topic": "execution",
                "data": [{
                    "execType": "Trade",
                    "execId": "PERP_EXEC001",
                    "orderLinkId": "coid-perp-1",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "execQty": "0.001",
                    "execPrice": "67000",
                    "execFee": "0.067",
                    "isMaker": False,
                    "execTime": "1700000000000",
                    "cumExecQty": "0.001",
                    "orderQty": "0.001",
                    "leavesQty": "0.0",
                    "markPrice": "67050.50",  # LINEAR-only field; proves perp decoder wired
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_make_perp_run_core_end_to_end_with_fake_stream():
    """make_bybit_perp_run_core emits a FillEvent with mark_price set from the linear execution frame."""
    ws = _PerpStreamWS()
    seen = []
    stop_flag = {"v": False}

    def _stop():
        # Stop after we've seen at least one FillEvent
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_bybit_perp_run_core(
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

    fill = fill_events[0]
    assert fill.trade_id == "PERP_EXEC001"
    assert fill.symbol == "BTCUSDT"

    # KEY assertion: mark_price must be set from markPrice field (proves map_bybit_perp is wired)
    assert fill.mark_price == pytest.approx(67050.50), (
        f"mark_price={fill.mark_price!r} — expected ~67050.50 (map_bybit_perp must be the decoder, "
        "not map_bybit_private which ignores markPrice)"
    )

    # WS must be closed after stop
    assert ws.closed is True, "ws.close() must be called when run_core exits"


def test_make_perp_run_core_no_connection_when_stop_immediate():
    """When stop() is True before the first iteration, run_user_data_forever exits without
    connecting at all — no ws.close() is called (never connected), but run_core must return cleanly."""
    connected = {"v": False}

    async def _spy_connect(url):  # noqa: ARG001
        connected["v"] = True
        return _PerpStreamWS()

    seen = []

    run_core = make_bybit_perp_run_core(
        ws_url="wss://x",
        api_key="K",
        api_secret="S",
        symbol="BTCUSDT",
        now_ms=lambda: 0,
        connect=_spy_connect,
    )

    # stop() True immediately — loop guard `while not stop()` exits without calling open_ws
    run_core(seen.append, lambda: True)

    assert not connected["v"], (
        "connect() must NOT be called when stop() is True from the start — "
        "run_user_data_forever exits the while-loop guard before open_ws"
    )
    assert seen == [], "no events expected when stop() is True from the start"
