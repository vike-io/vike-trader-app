"""vike.io OHLCV source tests — pure transforms + cursor paging (no network).

The candle values below are recorded from a live `ohlcv` MCP call
(BTC 5m, 2026-05-01 00:00 UTC) so the mapping is pinned to the real API shape.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.vike_source import candles_to_bars, collect_pages, fetch_bars_range


def test_candles_to_bars_maps_ohlcv():
    candles = [
        {"ts": 1777593600000, "open": 76346.58, "high": 76490.40, "low": 76320.42,
         "close": 76490.40, "volume": 140.67162},
        {"ts": 1777593900000, "open": 76490.39, "high": 76514.0, "low": 76452.15,
         "close": 76506.43, "volume": 34.10593},
    ]
    assert candles_to_bars(candles) == [
        Bar(ts=1777593600000, open=76346.58, high=76490.40, low=76320.42,
            close=76490.40, volume=140.67162),
        Bar(ts=1777593900000, open=76490.39, high=76514.0, low=76452.15,
            close=76506.43, volume=34.10593),
    ]


def test_candles_to_bars_defaults_missing_volume():
    assert candles_to_bars([{"ts": 1, "open": 1, "high": 2, "low": 0.5, "close": 1.5}]) == [
        Bar(ts=1, open=1.0, high=2.0, low=0.5, close=1.5, volume=0.0)
    ]


def test_collect_pages_follows_next_cursor_until_null():
    pages = [
        {"candles": [{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
         "next_cursor": "CURSOR1"},
        {"candles": [{"ts": 2, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 2}],
         "next_cursor": None},
    ]
    calls = []

    def fake(args):
        calls.append(args)
        return pages[len(calls) - 1]

    out = collect_pages({"symbol": "BTC", "interval": "1m"}, fake)
    assert [c["ts"] for c in out] == [1, 2]
    assert len(calls) == 2
    # the follow-up page must carry the cursor and the required symbol
    assert calls[1]["cursor"] == "CURSOR1"
    assert calls[1]["symbol"] == "BTC"


def test_collect_pages_single_page_no_cursor():
    resp = {"candles": [{"ts": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            "next_cursor": None}
    calls = []

    def fake(args):
        calls.append(args)
        return resp

    out = collect_pages({"symbol": "BTC", "interval": "1m"}, fake)
    assert len(out) == 1
    assert len(calls) == 1  # no extra call when next_cursor is null


def test_fetch_bars_range_returns_ordered_bars():
    resp = {
        "candles": [
            {"ts": 1777593600000, "open": 76346.58, "high": 76490.40, "low": 76320.42,
             "close": 76490.40, "volume": 140.67162},
            {"ts": 1777593900000, "open": 76490.39, "high": 76514.0, "low": 76452.15,
             "close": 76506.43, "volume": 34.10593},
        ],
        "next_cursor": None,
    }
    captured = {}

    def fake(args):
        captured.update(args)
        return resp

    bars = fetch_bars_range("BTC", "5m", 1777593600000, 1777594200000, caller=fake)
    assert bars == [
        Bar(ts=1777593600000, open=76346.58, high=76490.40, low=76320.42,
            close=76490.40, volume=140.67162),
        Bar(ts=1777593900000, open=76490.39, high=76514.0, low=76452.15,
            close=76506.43, volume=34.10593),
    ]
    # the request must pass symbol/interval and the time window through to the API
    assert captured["symbol"] == "BTC"
    assert captured["interval"] == "5m"
    assert captured["start"] == 1777593600000
    assert captured["end"] == 1777594200000
