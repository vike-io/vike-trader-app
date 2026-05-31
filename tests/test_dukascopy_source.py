"""Dukascopy forex source tests — pure transforms, no network. All times are UTC."""

import lzma
import struct
import urllib.error

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.data.dukascopy_source import (
    Tick,
    _retry,
    decode_ticks,
    decompress,
    hour_url,
    point_divisor,
    ticks_to_bars,
)

_REC = struct.Struct(">3i2f")  # ms, ask, bid, askVol, bidVol


def _http(code):
    return urllib.error.HTTPError("http://x", code, "msg", {}, None)


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}
    slept = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http(503)  # transient
        return b"ok"

    assert _retry(flaky, sleep=slept.append, base=0) == b"ok"
    assert calls["n"] == 3        # failed twice, succeeded on the third
    assert len(slept) == 2        # backed off before each retry


def test_retry_does_not_retry_permanent_404():
    calls = {"n": 0}

    def gone():
        calls["n"] += 1
        raise _http(404)

    with pytest.raises(urllib.error.HTTPError) as ei:
        _retry(gone, sleep=lambda s: None)
    assert ei.value.code == 404
    assert calls["n"] == 1        # 404 is permanent -> no retry


def test_retry_exhausts_then_raises_on_persistent_transient():
    calls = {"n": 0}

    def busy():
        calls["n"] += 1
        raise _http(503)

    with pytest.raises(urllib.error.HTTPError):
        _retry(busy, tries=3, sleep=lambda s: None)
    assert calls["n"] == 3        # tried exactly `tries` times


def test_point_divisor_jpy_vs_rest():
    assert point_divisor("EURUSD") == 1e5
    assert point_divisor("USDJPY") == 1e3
    assert point_divisor("eurjpy") == 1e3  # case-insensitive, by quote currency


def test_decompress_roundtrip():
    data = b"hello dukascopy" * 4
    assert decompress(lzma.compress(data)) == data


def test_decode_ticks_anchors_to_utc_hour_and_scales_price():
    # 2025-01-02 10:00:00 UTC in epoch ms.
    hour = 1735812000000
    raw = _REC.pack(175, 103518, 103514, 1.0, 2.0) + _REC.pack(281, 103519, 103515, 3.0, 4.0)
    ticks = decode_ticks(raw, hour, divisor=1e5)
    assert ticks == [
        Tick(ts=hour + 175, bid=1.03514, ask=1.03518, bid_vol=2.0, ask_vol=1.0),
        Tick(ts=hour + 281, bid=1.03515, ask=1.03519, bid_vol=4.0, ask_vol=3.0),
    ]


def test_ticks_to_bars_aggregates_mid_into_ohlc_with_tick_volume():
    # bucket 0: [0, 60000)  ;  bucket 1: [60000, 120000). mid = (bid+ask)/2 (exact halves).
    ticks = [
        Tick(ts=0, bid=1.0, ask=1.5, bid_vol=0, ask_vol=0),       # mid 1.25
        Tick(ts=30_000, bid=1.25, ask=1.75, bid_vol=0, ask_vol=0),  # mid 1.5
        Tick(ts=60_000, bid=0.5, ask=1.5, bid_vol=0, ask_vol=0),   # mid 1.0
    ]
    assert ticks_to_bars(ticks, 60_000) == [
        Bar(ts=0, open=1.25, high=1.5, low=1.25, close=1.5, volume=2.0),
        Bar(ts=60_000, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0),
    ]


def test_hour_url_is_utc_with_zero_indexed_month():
    # 2025-01-02 10:00 UTC -> January is month "00", not "01".
    assert hour_url("eurusd", 1735812000000) == (
        "https://datafeed.dukascopy.com/datafeed/EURUSD/2025/00/02/10h_ticks.bi5"
    )
    # 2025-12-31 23:00 UTC -> December is month "11".
    assert hour_url("USDJPY", 1767222000000).endswith("/2025/11/31/23h_ticks.bi5")
