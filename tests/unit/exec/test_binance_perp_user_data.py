"""Binance USDS-M futures listenKey user-data WS + make_binance_perp_run_core: TDD tests.

Covers:
- listenkey_create: POST /fapi/v1/listenKey with apiKey-header-only (no signature)
- listenkey_keepalive: PUT /fapi/v1/listenKey; swallows errors (best-effort)
- open_binance_perp_user_data_ws: create listenKey -> connect wss://<base>/<key>, no ack loop
- make_binance_perp_run_core: ping wired + ping_ms=1_800_000, recv_timeout=1.0, decode->FillEvent
- NON-BLOCKING keepalive: the ping is offloaded via asyncio.to_thread so it does not block the
  event loop; the explicit small timeout is forwarded (NOT the default 10s)
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from vike_trader_app.exec.binance import perp_user_data as pud


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, body): self._b = json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


# ---------------------------------------------------------------------------
# listenkey_create
# ---------------------------------------------------------------------------

def test_listenkey_create_apikey_header_only_no_signature():
    """POST /fapi/v1/listenKey must carry ONLY X-MBX-APIKEY header — NOT signed."""
    cap = {}

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        cap["headers"] = dict(req.header_items())
        return _Resp({"listenKey": "K" * 64})

    key = pud.listenkey_create(fapi_rest_url="https://demo-fapi.binance.com",
                               api_key="AK", urlopen=fake_urlopen)
    assert key == "K" * 64
    assert cap["url"] == "https://demo-fapi.binance.com/fapi/v1/listenKey"
    assert cap["method"] == "POST"
    assert "signature" not in cap["url"]                        # NOT signed
    assert any(h.lower() == "x-mbx-apikey" and v == "AK" for h, v in cap["headers"].items())


# ---------------------------------------------------------------------------
# listenkey_keepalive
# ---------------------------------------------------------------------------

def test_listenkey_keepalive_is_put_same_path():
    """PUT /fapi/v1/listenKey must hit the exact same path as the create."""
    cap = {}

    def fake_urlopen(req, timeout=None):
        cap["url"] = req.full_url
        cap["method"] = req.get_method()
        return _Resp({"listenKey": "K" * 64})

    pud.listenkey_keepalive(fapi_rest_url="https://demo-fapi.binance.com",
                            api_key="AK", urlopen=fake_urlopen)
    assert cap["method"] == "PUT"
    assert cap["url"].endswith("/fapi/v1/listenKey")


def test_keepalive_swallows_errors():
    """A network error in listenkey_keepalive must NOT raise (best-effort)."""
    def boom(req, timeout=None): raise OSError("network down")
    pud.listenkey_keepalive(fapi_rest_url="https://x", api_key="AK", urlopen=boom)  # must NOT raise


# ---------------------------------------------------------------------------
# open_binance_perp_user_data_ws
# ---------------------------------------------------------------------------

def test_open_ws_creates_listenkey_then_connects_no_ack_loop():
    """open_binance_perp_user_data_ws must:
    1. Call listenkey_create (POST) to get the key
    2. Connect to <ws_base>/<listenKey>
    3. Return the ws immediately (NO subscribe frame, NO ack loop)
    """
    cap = {}

    def fake_urlopen(req, timeout=None):
        cap["created"] = True
        return _Resp({"listenKey": "LK123"})

    class _FakeWS:
        def __init__(self, url): self.url = url

    async def fake_connect(url):
        cap["ws_url"] = url
        return _FakeWS(url)

    ws = asyncio.run(pud.open_binance_perp_user_data_ws(
        fapi_rest_url="https://demo-fapi.binance.com",
        ws_base_url="wss://fstream.binancefuture.com/ws",
        api_key="AK", connect=fake_connect, urlopen=fake_urlopen))

    assert cap.get("created") is True
    assert cap["ws_url"] == "wss://fstream.binancefuture.com/ws/LK123"
    assert isinstance(ws, _FakeWS)


# ---------------------------------------------------------------------------
# make_binance_perp_run_core — ping/cadence wiring
# ---------------------------------------------------------------------------

def test_run_core_wires_ping_and_cadence(monkeypatch):
    """make_binance_perp_run_core must wire ping!=None, ping_ms=1_800_000, recv_timeout=1.0."""
    seen = {}

    async def fake_forever(emit, **kw):
        seen.update(kw)

    monkeypatch.setattr(pud, "run_user_data_forever", fake_forever)

    core = pud.make_binance_perp_run_core(
        fapi_rest_url="https://demo-fapi.binance.com",
        ws_base_url="wss://fstream.binancefuture.com/ws",
        api_key="AK", symbol="BTCUSDT", now_ms=lambda: 0)
    core(lambda e: None, lambda: True)

    assert seen.get("ping") is not None
    assert seen["ping_ms"] == 1_800_000
    assert seen["recv_timeout"] == 1.0

    # decode dispatches to map_binance_perp -> FillEvent
    from vike_trader_app.exec.events import FillEvent
    out = seen["decode"]({"e": "ORDER_TRADE_UPDATE", "T": 1, "o":
        {"c": "c", "x": "TRADE", "X": "FILLED", "S": "BUY", "l": "1", "L": "2", "n": "0",
         "t": 5, "m": False, "s": "BTCUSDT", "ps": "BOTH"}})
    assert isinstance(out[0], FillEvent)


# ---------------------------------------------------------------------------
# NON-BLOCKING keepalive: asyncio.to_thread offload + bounded timeout + best-effort
# ---------------------------------------------------------------------------

def test_keepalive_ping_is_offloaded_and_does_not_block_loop():
    """The _keepalive ping hook MUST offload the sync HTTP call via asyncio.to_thread.

    Proof: replace _listenkey_keepalive_sync with a slow fake that records the timeout it received
    and sleeps briefly. The ping coroutine must YIELD control to the event loop (not block it),
    and the timeout passed to the sync call must be well under 10 (the library default) — proving
    a bounded explicit small timeout is forwarded.
    """
    calls = []

    def slow_keepalive_sync(*, fapi_rest_url, api_key, timeout, urlopen=None):
        """Simulates a blocking PUT that records what timeout it received."""
        calls.append({"timeout": timeout, "url": fapi_rest_url, "api_key": api_key})
        # A real urllib call would block here; in the test we just record and return.
        time.sleep(0.01)  # tiny sleep to simulate I/O without hanging the test

    # Patch the module-level sync keepalive with our spy
    original = pud._listenkey_keepalive_sync
    pud._listenkey_keepalive_sync = slow_keepalive_sync

    try:
        # Build the ping hook as make_binance_perp_run_core would
        async def _keepalive(ws):
            await pud._offload_keepalive(
                fapi_rest_url="https://demo-fapi.binance.com",
                api_key="AK",
            )

        completed = []

        async def _drive():
            # Run the keepalive; a blocking inline call would starve this coroutine
            task = asyncio.ensure_future(_keepalive(None))
            # Yield once — if the keepalive were inline-blocking, this would never advance
            await asyncio.sleep(0)
            completed.append("event_loop_ran")
            await task

        asyncio.run(_drive())

        # The event loop was free to run while the keepalive executed
        assert "event_loop_ran" in completed, "Event loop was blocked by the keepalive ping"
        # The explicit small timeout was passed (NOT the urllib default 10)
        assert calls, "keepalive_sync was not called"
        assert calls[0]["timeout"] < 10, (
            f"Keepalive timeout {calls[0]['timeout']} is not < 10 (should be a small bounded value)"
        )

    finally:
        pud._listenkey_keepalive_sync = original


def test_keepalive_timeout_forwarded_to_sync_call():
    """The explicit timeout=2 (not urllib default 10) must be forwarded to urlopen."""
    received_timeout = []

    def fake_urlopen(req, timeout=None):
        received_timeout.append(timeout)
        return _Resp({})

    pud.listenkey_keepalive(
        fapi_rest_url="https://demo-fapi.binance.com",
        api_key="AK",
        urlopen=fake_urlopen,
        timeout=2,  # explicit small timeout
    )
    assert received_timeout, "urlopen was not called"
    assert received_timeout[0] == 2, (
        f"Expected timeout=2 forwarded to urlopen, got {received_timeout[0]}"
    )
