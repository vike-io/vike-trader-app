"""make_okx_perp_run_core: TDD tests.

Covers:
- End-to-end make_okx_perp_run_core with a fake SWAP orders frame:
  * FillEvent.last_qty is rescaled (contracts × ct_val), proving map_okx_perp + ct_val are wired
  * FillEvent.mark_price is set from fillMarkPx
  * FillEvent.position_side == "BOTH" (from posSide="net")
  * subscribe frame carries instType="SWAP"
  * ws.closed is True after stop
- No-connect-when-stop-immediate guard (mirrors bybit test)
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.okx.perp_user_data import make_okx_perp_run_core
from vike_trader_app.exec.events import FillEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _async_return(value):
    async def _c(url):
        return value
    return _c


class _PerpStreamWS:
    """WS that completes the OKX handshake, then emits one SWAP orders fill frame."""

    def __init__(self):
        self._frames = [
            json.dumps({"event": "login", "code": "0"}),
            json.dumps({"event": "subscribe", "arg": {"channel": "orders"}}),
            json.dumps({
                "arg": {"channel": "orders", "instType": "SWAP"},
                "data": [{
                    "tradeId": "PERP_T1",
                    "instId": "BTC-USDT-SWAP",
                    "fillSz": "5",
                    "fillPx": "67000",
                    "fillFee": "-0.5",
                    "side": "buy",
                    "state": "filled",
                    "execType": "T",
                    "fillTime": "1700000000000",
                    "clOrdId": "c-1",
                    "ordId": "9",
                    "sz": "5",
                    "accFillSz": "5",
                    "posSide": "net",
                    "fillMarkPx": "67050.5",
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

def test_make_perp_run_core_end_to_end():
    """make_okx_perp_run_core emits a FillEvent from a SWAP orders frame with:
    - last_qty rescaled contracts->base via ct_val (5 × 0.01 = 0.05)
    - mark_price from fillMarkPx
    - position_side == "BOTH" from posSide="net"
    - subscribe frame carries instType="SWAP"
    - ws.closed is True after stop
    """
    ws = _PerpStreamWS()
    seen = []
    stop_flag = {"v": False}

    def _stop():
        if any(isinstance(e, FillEvent) for e in seen):
            stop_flag["v"] = True
        return stop_flag["v"]

    run_core = make_okx_perp_run_core(
        ws_url="wss://x",
        api_key="K",
        api_secret="S",
        passphrase="P",
        symbol="BTC-USDT-SWAP",
        ct_val=0.01,
        now_ms=lambda: 0,
        connect=_async_return(ws),
    )

    run_core(seen.append, _stop)

    fill_events = [e for e in seen if isinstance(e, FillEvent)]
    assert fill_events, f"Expected at least one FillEvent, got: {[type(e).__name__ for e in seen]}"

    fill = fill_events[0]
    assert fill.trade_id == "PERP_T1"
    assert fill.symbol == "BTC-USDT-SWAP"

    # KEY: last_qty rescaled from contracts to base (5 contracts × 0.01 ct_val = 0.05 BTC)
    # If map_okx_private were used instead, last_qty would be 5.0 (no rescaling)
    assert fill.last_qty == pytest.approx(0.05), (
        f"last_qty={fill.last_qty!r} — expected ~0.05 (5 contracts × ct_val=0.01); "
        "map_okx_perp must be the decoder, not map_okx_private"
    )

    # mark_price from fillMarkPx
    assert fill.mark_price == pytest.approx(67050.5), (
        f"mark_price={fill.mark_price!r} — expected ~67050.5 from fillMarkPx"
    )

    # position_side from posSide="net" -> "BOTH"
    assert fill.position_side == "BOTH", (
        f"position_side={fill.position_side!r} — expected 'BOTH' from posSide='net'"
    )

    # subscribe frame must carry instType="SWAP"
    sub = ws.subscribe_frame()
    inst_type_sent = sub["args"][0]["instType"]
    assert inst_type_sent == "SWAP", (
        f"perp run_core must subscribe instType='SWAP', got {inst_type_sent!r}"
    )

    # WS closed after stop
    assert ws.closed is True, "ws.close() must be called when run_core exits"


def test_make_perp_run_core_no_connect_when_stop_immediate():
    """When stop() is True from the start, connect() is never called and seen is empty."""
    connected = {"v": False}

    async def _spy_connect(url):  # noqa: ARG001
        connected["v"] = True
        return _PerpStreamWS()

    seen = []

    run_core = make_okx_perp_run_core(
        ws_url="wss://x",
        api_key="K",
        api_secret="S",
        passphrase="P",
        symbol="BTC-USDT-SWAP",
        ct_val=0.01,
        now_ms=lambda: 0,
        connect=_spy_connect,
    )

    run_core(seen.append, lambda: True)

    assert not connected["v"], (
        "connect() must NOT be called when stop() is True from the start"
    )
    assert seen == [], "no events expected when stop() is True from the start"
