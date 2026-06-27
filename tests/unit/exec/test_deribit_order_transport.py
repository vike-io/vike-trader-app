"""DeribitOrderTransport: a persistent authed order WS behind the 6a injected transport seam.

Covers:
- connect() sends public/auth (client_credentials) and AWAITS the auth ack by id
- __call__(method, params) sends the request frame and returns the parsed {id,result|error}
  dict matched by id — the SAME shape the 6a fake returns (DeribitExecutionClient.submit works)
- recv-until-matching-id skips an interleaved frame whose id != the awaited id
- a bounded request deadline raises (a stalled socket can't hang the GUI main thread)
- close() is bounded + idempotent and never raises (teardown must not block)
- secrets (client_id/client_secret) never appear in a raised error
- the transport drives the REAL DeribitExecutionClient.submit end-to-end (drop-in proof)
"""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.bus import EventBus
from vike_trader_app.exec.deribit.client import DeribitExecutionClient
from vike_trader_app.exec.deribit.transport import DeribitOrderTransport
from vike_trader_app.exec.events import OrderAccepted, OrderRequest, OrderSubmitted


# --- scripted fake ws (one socket, send + recv-by-id) ----------------------
class _FakeWS:
    """Async ws stub. ``replies`` maps an awaited request-id -> the raw JSON string to return
    on the NEXT recv after that id's frame is sent. ``extra`` frames (no id / wrong id) are
    yielded BEFORE the matching reply to exercise the recv-until-id skip."""

    def __init__(self):
        self.sent: list[str] = []
        self._queue: list[str] = []   # frames recv() will yield, in order
        self.closed = False

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._queue:
            return self._queue.pop(0)
        await asyncio.sleep(3600)            # idle — models a quiet socket
        raise AssertionError("unreachable")  # pragma: no cover

    async def close(self):
        self.closed = True

    # test helpers
    def enqueue(self, raw: str):
        self._queue.append(raw)


def _connect_factory(ws):
    async def _c(url):
        return ws
    return _c


def _auth_ok(rid):
    return json.dumps({"jsonrpc": "2.0", "id": rid,
                       "result": {"access_token": "AT", "refresh_token": "RT"}})


def _make_transport(ws, request_timeout=1.0, **kw):
    return DeribitOrderTransport(
        ws_url="wss://fake", client_id="cid", client_secret="csec",
        connect=_connect_factory(ws), request_timeout=request_timeout, **kw)


# --- connect() handshake ---------------------------------------------------
def test_connect_sends_auth_and_awaits_ack():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))            # auth uses builder id=1
    t = _make_transport(ws)
    t.connect()
    auth = json.loads(ws.sent[0])
    assert auth["method"] == "public/auth"
    assert auth["params"]["grant_type"] == "client_credentials"
    assert auth["params"]["client_id"] == "cid"
    assert auth["id"] == 1
    t.close()


# --- __call__ request/response by id --------------------------------------
def test_call_returns_parsed_result_by_id():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws)
    t.connect()
    # The next request gets id=2 from the shared builder.
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "id": 2,
                           "result": {"order": {"order_id": "OID-9"}, "trades": []}}))
    resp = t("private/buy", {"instrument_name": "BTC-27JUN25-60000-C", "amount": 1.0})
    req = json.loads(ws.sent[1])
    assert req["method"] == "private/buy"
    assert req["id"] == 2
    assert resp["result"]["order"]["order_id"] == "OID-9"
    t.close()


def test_call_skips_non_matching_id_then_returns_match():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws)
    t.connect()
    # A stray frame (subscription notification, no id) and a wrong-id reply precede the match.
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "method": "subscription",
                           "params": {"channel": "x", "data": []}}))
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "id": 999, "result": {"stale": True}}))
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}}))
    resp = t("private/cancel", {"order_id": "OID-9"})
    assert resp["result"] == {"ok": True}
    t.close()


def test_call_error_frame_passes_through_without_secret():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws)
    t.connect()
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "id": 2,
                           "error": {"code": 11044, "message": "not_open_order"}}))
    resp = t("private/cancel", {"order_id": "OID-9"})
    assert resp["error"]["code"] == 11044     # DeribitExecutionClient.cancel swallows this code
    t.close()


# --- bounded deadline ------------------------------------------------------
def test_call_times_out_when_no_matching_reply():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws, request_timeout=0.1)
    t.connect()
    # No reply enqueued for the request id -> recv idles -> wait_for fires.
    with pytest.raises(TimeoutError):
        t("private/buy", {"instrument_name": "X", "amount": 1.0})
    t.close()


# --- close() bounded + idempotent -----------------------------------------
def test_close_is_idempotent_and_closes_ws():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws)
    t.connect()
    t.close()
    assert ws.closed is True
    t.close()   # second close must not raise


def test_close_swallows_a_raising_ws_close():
    class _BoomCloseWS(_FakeWS):
        async def close(self):
            raise RuntimeError("socket gone")
    ws = _BoomCloseWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws)
    t.connect()
    t.close()   # must not raise


def test_auth_failure_raises_without_secret():
    _SECRET = "SUPERSECRETKEY99"
    ws = _FakeWS()
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "id": 1,
                           "error": {"code": 13004, "message": "invalid_credentials"}}))
    t = DeribitOrderTransport(ws_url="wss://fake", client_id="cid", client_secret=_SECRET,
                              connect=_connect_factory(ws), request_timeout=1.0)
    with pytest.raises(Exception) as exc_info:   # noqa: PT011 — assert no secret leak below
        t.connect()
    assert _SECRET not in str(exc_info.value)
    t.close()
    assert ws.closed is True


# --- drop-in proof: drive the REAL 6a client through the transport --------
def test_real_client_submit_through_transport():
    ws = _FakeWS()
    ws.enqueue(_auth_ok(1))
    t = _make_transport(ws)
    t.connect()
    ws.enqueue(json.dumps({"jsonrpc": "2.0", "id": 2,
                           "result": {"order": {"order_id": "OID-42"}, "trades": []}}))
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    client = DeribitExecutionClient(
        bus, transport=t, symbol="BTC-27JUN25-60000-C",
        filters={"tick_size": 0.0001, "step_size": 0.1, "min_qty": 0.1,
                 "max_qty": 0.0, "min_notional": 0.0},
        currency="BTC")
    client.submit(OrderRequest(client_order_id="sess-0", venue="deribit",
                               symbol="BTC-27JUN25-60000-C", side=+1, qty=1.0,
                               order_type="limit", price=0.05))
    assert [type(e).__name__ for e in seen] == ["OrderSubmitted", "OrderAccepted"]
    acc = [e for e in seen if isinstance(e, OrderAccepted)][0]
    assert acc.venue_order_id == "OID-42"
    t.close()
