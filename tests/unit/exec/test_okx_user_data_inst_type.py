"""inst_type threading tests for make_okx_run_core + open_okx_user_data_ws.

Covers:
- REGRESSION GUARD: make_okx_run_core with no inst_type arg subscribes instType="SPOT" (default unchanged)
- THREADING: make_okx_run_core(inst_type="SWAP") forwards SWAP into the subscribe frame
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.okx.user_data import make_okx_run_core
from vike_trader_app.exec.events import FillEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _async_return(value):
    async def _c(url):
        return value
    return _c


class _StreamWS:
    """WS that completes the OKX handshake then emits one SPOT fill, then goes idle.

    Records all sent frames in self.sent for inspection.
    """

    def __init__(self, inst_type: str = "SPOT"):
        self._frames = [
            json.dumps({"event": "login", "code": "0"}),
            json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}),
            # One orders frame so run_core has something to decode
            json.dumps({
                "arg": {"channel": "orders", "instType": inst_type},
                "data": [{
                    "tradeId": "T99",
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
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(10)
        raise asyncio.TimeoutError

    async def close(self) -> None:
        self.closed = True

    def subscribe_frame(self) -> dict:
        """Return the parsed subscribe frame from sent frames."""
        for raw in self.sent:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("op") == "subscribe":
                return parsed
        raise AssertionError(f"No subscribe frame found in sent: {self.sent!r}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_spot_run_core_subscribes_spot():
    """REGRESSION GUARD: make_okx_run_core with NO inst_type arg must subscribe instType='SPOT'.

    This proves the default='SPOT' keeps the existing spot path byte-identical after the edit.
    """
    ws = _StreamWS(inst_type="SPOT")
    seen = []
    stop_flag = {"v": False}

    def _stop():
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
        # NO inst_type arg — must default to "SPOT"
    )
    run_core(seen.append, _stop)

    sub = ws.subscribe_frame()
    inst_type_sent = sub["args"][0]["instType"]
    assert inst_type_sent == "SPOT", (
        f"REGRESSION: spot run_core (no inst_type arg) must subscribe instType='SPOT', got {inst_type_sent!r}"
    )


def test_run_core_threads_inst_type_swap():
    """make_okx_run_core(inst_type='SWAP') must forward SWAP into the subscribe frame.

    This FAILS before the edit (inst_type was hardcoded SPOT); GREEN after the edit.
    """
    ws = _StreamWS(inst_type="SWAP")
    seen = []
    stop_flag = {"v": False}

    def _stop():
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_okx_run_core(
        ws_url="wss://x",
        api_key="K",
        api_secret="S",
        passphrase="P",
        symbol="BTC-USDT-SWAP",
        now_ms=lambda: 0,
        connect=_async_return(ws),
        inst_type="SWAP",
    )
    run_core(seen.append, _stop)

    sub = ws.subscribe_frame()
    inst_type_sent = sub["args"][0]["instType"]
    assert inst_type_sent == "SWAP", (
        f"make_okx_run_core(inst_type='SWAP') must subscribe instType='SWAP', got {inst_type_sent!r}"
    )
