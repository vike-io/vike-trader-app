"""Venue-neutral run_user_data_forever async core: open_ws + decode callback interface."""
from __future__ import annotations

import asyncio
import json

import pytest

from vike_trader_app.exec.user_data_core import (
    UserDataAuthError,
    run_user_data_forever,
)


class _FakeWS:
    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False
        self.pings_sent = 0

    async def recv(self):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(0.01)
        raise TimeoutError  # idle -> recv timeout path

    async def close(self):
        self.closed = True


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=2.0))


# ---------------------------------------------------------------------------
# test_emits_decoded_events_then_stops
# ---------------------------------------------------------------------------

def test_emits_decoded_events_then_stops():
    sentinel_a = object()
    sentinel_b = object()
    frames = [
        json.dumps({"t": 1}),
        json.dumps({"t": 2}),
    ]
    ws = _FakeWS(frames)
    seen = []
    stop_flag = {"v": False}

    def _stop():
        if not ws._frames:
            stop_flag["v"] = True
        return stop_flag["v"]

    def _decode(frame):
        return [sentinel_a] if frame.get("t") == 1 else [sentinel_b]

    async def _open_ws():
        return ws

    _run(run_user_data_forever(
        seen.append,
        open_ws=_open_ws,
        decode=_decode,
        stop=_stop,
        recv_timeout=0.05,
    ))

    assert seen == [sentinel_a, sentinel_b], f"Expected both sentinels in order, got {seen}"
    assert ws.closed is True, "ws.close() must be called in finally"


# ---------------------------------------------------------------------------
# test_auth_error_raises_not_loops
# ---------------------------------------------------------------------------

def test_auth_error_raises_not_loops():
    async def _open_ws():
        raise UserDataAuthError("bad credentials")

    with pytest.raises(UserDataAuthError):
        _run(run_user_data_forever(
            lambda e: None,
            open_ws=_open_ws,
            decode=lambda f: [],
            stop=lambda: False,
        ))


# ---------------------------------------------------------------------------
# test_idle_stream_unblocks_on_stop (0xC0000409 invariant)
# ---------------------------------------------------------------------------

def test_idle_stream_unblocks_on_stop():
    """An idle stream (no frames, always TimeoutError) must unblock when stop() returns True."""
    ws = _FakeWS([])  # never yields a real frame
    call_count = {"n": 0}

    def _stop():
        call_count["n"] += 1
        return call_count["n"] >= 2  # True on second poll

    async def _open_ws():
        return ws

    # Must complete inside the 2-second wait_for — if recv never times out this deadlocks
    _run(run_user_data_forever(
        lambda e: None,
        open_ws=_open_ws,
        decode=lambda f: [],
        stop=_stop,
        recv_timeout=0.05,
    ))

    assert ws.closed is True, "ws.close() must be called even when idle"


# ---------------------------------------------------------------------------
# test_ping_sent_on_cadence
# ---------------------------------------------------------------------------

def test_ping_sent_on_cadence():
    """Ping is sent when now_ms() - last_ping >= ping_ms."""
    # Clock: first call returns 0 (connect time), second returns ping_ms+1 (past threshold)
    clock = [0, 0, 30_001, 30_001, 30_001]
    clock_iter = iter(clock)
    pings = []

    frames = [json.dumps({"t": 1})]
    ws = _FakeWS(frames)
    stop_flag = {"v": False}

    def _stop():
        if not ws._frames and len(pings) >= 1:
            stop_flag["v"] = True
        return stop_flag["v"]

    async def _ping(w):
        pings.append(True)

    async def _open_ws():
        return ws

    def _now_ms():
        try:
            return next(clock_iter)
        except StopIteration:
            return 99_999  # well past threshold

    _run(run_user_data_forever(
        lambda e: None,
        open_ws=_open_ws,
        decode=lambda f: [],
        stop=_stop,
        ping=_ping,
        ping_ms=30_000,
        now_ms=_now_ms,
        recv_timeout=0.05,
    ))

    assert len(pings) >= 1, "Expected at least one ping to be sent when cadence elapsed"


# ---------------------------------------------------------------------------
# test_no_spurious_ping_before_cadence
# ---------------------------------------------------------------------------

def test_no_spurious_ping_before_cadence():
    """No ping should fire in the first iterations when < ping_ms has elapsed since connect."""
    # Clock never advances past ping_ms (always returns 0)
    pings = []

    frames = [json.dumps({"t": 1}), json.dumps({"t": 2})]
    ws = _FakeWS(frames)
    stop_flag = {"v": False}

    def _stop():
        if not ws._frames:
            stop_flag["v"] = True
        return stop_flag["v"]

    async def _ping(w):
        pings.append(True)

    async def _open_ws():
        return ws

    _run(run_user_data_forever(
        lambda e: None,
        open_ws=_open_ws,
        decode=lambda f: [],
        stop=_stop,
        ping=_ping,
        ping_ms=30_000,
        now_ms=lambda: 0,   # clock frozen at t=0, so now_ms()-last_ping == 0 always < ping_ms
        recv_timeout=0.05,
    ))

    assert pings == [], f"Expected no spurious pings when elapsed < ping_ms, got {len(pings)}"


# ---------------------------------------------------------------------------
# test_reconnect_on_transport_hiccup
# ---------------------------------------------------------------------------

def test_reconnect_on_transport_hiccup():
    """A transport error on first open_ws triggers backoff reconnect; second attempt succeeds."""
    sentinel = object()
    seen = []
    call_count = {"n": 0}

    frames = [json.dumps({"ok": True})]
    ws = _FakeWS(frames)
    stop_flag = {"v": False}

    def _stop():
        if not ws._frames:
            stop_flag["v"] = True
        return stop_flag["v"]

    async def _open_ws():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("boom — transport hiccup")
        return ws

    def _decode(frame):
        return [sentinel] if frame.get("ok") else []

    _run(run_user_data_forever(
        seen.append,
        open_ws=_open_ws,
        decode=_decode,
        stop=_stop,
        max_backoff=0.01,   # keep test fast
        recv_timeout=0.05,
    ))

    assert seen == [sentinel], f"Expected sentinel after reconnect, got {seen}"


# ---------------------------------------------------------------------------
# test_backoff_wakes_on_stop (Fix 1b — backoff sleep must poll stop())
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# test_non_json_frame_skipped  (OKX text 'pong' keepalive guard)
# ---------------------------------------------------------------------------

def test_non_json_frame_skipped():
    """A non-JSON frame (e.g. OKX text 'pong') must be skipped without reconnecting.

    The real JSON frame that follows must still be decoded and emitted, proving the
    core does NOT treat a parse error as a transport hiccup.  open_ws is called exactly
    once (no reconnect loop).
    """
    sentinel = object()
    seen = []
    open_ws_calls = {"n": 0}
    stop_flag = {"v": False}

    frames = ["pong", json.dumps({"real": True})]
    ws = _FakeWS(frames)

    def _stop():
        if not ws._frames:
            stop_flag["v"] = True
        return stop_flag["v"]

    def _decode(frame):
        return [sentinel] if frame.get("real") else []

    async def _open_ws():
        open_ws_calls["n"] += 1
        return ws

    _run(run_user_data_forever(
        seen.append,
        open_ws=_open_ws,
        decode=_decode,
        stop=_stop,
        recv_timeout=0.05,
    ))

    assert open_ws_calls["n"] == 1, (
        f"Expected exactly 1 open_ws call (no reconnect on non-JSON frame), got {open_ws_calls['n']}"
    )
    assert seen == [sentinel], (
        f"Expected the JSON frame's decoded event to be emitted, got {seen}"
    )


def test_backoff_wakes_on_stop():
    """A stop arriving mid-backoff must be seen within ~recv_timeout, not after the full backoff.

    open_ws raises a transport error on the first call (entering the 1.0s initial backoff);
    stop() flips True shortly after the backoff begins. The pump must return well before the
    full 1.0s backoff elapses — chunked sleep polls stop() every recv_timeout.
    """
    import time

    started = {"t": None}

    async def _open_ws():
        started["t"] = time.monotonic()
        raise ValueError("boom — transport hiccup")  # always fails -> backoff path

    def _stop():
        # False until ~0.05s after the first (failing) connect, then True
        if started["t"] is None:
            return False
        return (time.monotonic() - started["t"]) >= 0.05

    t0 = time.monotonic()
    _run(run_user_data_forever(
        lambda e: None,
        open_ws=_open_ws,
        decode=lambda f: [],
        stop=_stop,
        recv_timeout=0.05,
        # default max_backoff=30, initial backoff=1.0 — without chunking this sleeps the full 1.0s
    ))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"backoff did not wake on stop: returned after {elapsed:.3f}s (full backoff is 1.0s)"
