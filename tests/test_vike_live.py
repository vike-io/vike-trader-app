"""LiveBarFeed: parse stream frames, ping/pong, de-dupe, seq-gap -> REST backfill.

The pure frame-handling core is tested here with scripted frames (no network); the async
websocket transport (`run`) is a thin I/O shell, exercised live via tmp/ws_probe.py.
"""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.data.vike_live import LiveBarFeed, LiveFeedError, bar_from_frame


def _frame(ts, *, closed=True, seq=None, c=100.0, funding=None):
    return {
        "type": "bar", "symbol": "BTCUSDT", "interval": "1m", "ts": ts,
        "open": c, "high": c + 1, "low": c - 1, "close": c, "volume": 2.0,
        "funding": funding, "closed": closed, "seq": seq,
    }


def _feed(backfill=None):
    return LiveBarFeed("BTCUSDT", "1m", backfill=backfill)


# --- bar_from_frame -----------------------------------------------------------

def test_bar_from_frame_maps_fields_and_funding():
    b = bar_from_frame(_frame(60_000, c=73890.5, funding=0.0001))
    assert (b.ts, b.open, b.close, b.volume, b.funding) == (60_000, 73890.5, 73890.5, 2.0, 0.0001)


def test_bar_from_frame_null_funding_stays_none():
    assert bar_from_frame(_frame(0, funding=None)).funding is None


# --- handle_frame -------------------------------------------------------------

def test_closed_bar_is_emitted():
    feed, out = _feed(), []
    feed.handle_frame(_frame(60_000, seq=1), out.append)
    assert [b.ts for b in out] == [60_000]


def test_forming_bar_is_not_emitted_but_stored_for_painting():
    feed, out = _feed(), []
    feed.handle_frame(_frame(120_000, closed=False, seq=2, c=99.0), out.append)
    assert out == []
    assert feed.forming.ts == 120_000 and feed.forming.close == 99.0


def test_ping_frame_requests_a_pong_and_emits_nothing():
    feed, out = _feed(), []
    reply = feed.handle_frame({"type": "ping"}, out.append)
    assert reply == "pong"
    assert out == []


def test_duplicate_or_older_ts_is_not_re_emitted():
    feed, out = _feed(), []
    feed.handle_frame(_frame(60_000, seq=1), out.append)
    feed.handle_frame(_frame(60_000, seq=2), out.append)  # same ts
    feed.handle_frame(_frame(30_000, seq=3), out.append)  # older ts
    assert [b.ts for b in out] == [60_000]


def test_error_frame_raises():
    feed = _feed()
    with pytest.raises(LiveFeedError):
        feed.handle_frame({"type": "error", "code": "unauthorized", "message": "bad token"}, lambda b: None)


def test_seq_gap_triggers_rest_backfill_of_the_hole_then_the_new_bar():
    calls = {}

    def backfill(symbol, interval, start_ms, end_ms):
        calls["args"] = (symbol, interval, start_ms, end_ms)
        return [Bar(ts=120_000, open=1, high=1, low=1, close=1, volume=1),
                Bar(ts=180_000, open=1, high=1, low=1, close=1, volume=1)]

    feed, out = _feed(backfill=backfill), []
    feed.handle_frame(_frame(60_000, seq=1), out.append)   # last_ts=60_000, last_seq=1
    feed.handle_frame(_frame(240_000, seq=4), out.append)  # missed seq 2,3 -> bars 120k,180k

    assert [b.ts for b in out] == [60_000, 120_000, 180_000, 240_000]  # hole filled, in order
    assert calls["args"] == ("BTCUSDT", "1m", 60_001, 240_000)  # (last_ts, new_ts]


def test_forming_frame_seq_does_not_cause_a_false_gap():
    feed, out = _feed(backfill=lambda *a: pytest.fail("backfill should not run")), []
    feed.handle_frame(_frame(60_000, seq=1), out.append)
    feed.handle_frame(_frame(90_000, closed=False, seq=2), out.append)  # forming, advances seq
    feed.handle_frame(_frame(120_000, seq=3), out.append)               # in-sequence -> no gap
    assert [b.ts for b in out] == [60_000, 120_000]
