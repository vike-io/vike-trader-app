"""Yahoo forex source tests — pure transforms, no network (chart fetcher injected)."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.data.yahoo_source import (
    chart_to_bars,
    fetch_bars_range,
    make_yahoo_fetch_latest,
    quote_from_payload,
    yahoo_symbol,
)


def _payload(timestamps, opens, highs, lows, closes, volumes=None, price=1.2345, error=None):
    return {
        "chart": {
            "error": error,
            "result": None if error else [{
                "meta": {"regularMarketPrice": price},
                "timestamp": timestamps,
                "indicators": {"quote": [{
                    "open": opens, "high": highs, "low": lows, "close": closes,
                    "volume": volumes if volumes is not None else [0] * len(timestamps),
                }]},
            }],
        }
    }


def test_yahoo_symbol_appends_suffix_and_is_idempotent():
    assert yahoo_symbol("eurusd") == "EURUSD=X"
    assert yahoo_symbol("EURUSD=X") == "EURUSD=X"


def test_chart_to_bars_parses_and_converts_seconds_to_ms():
    p = _payload([1609459200, 1609459260],
                 [1.0, 1.1], [1.2, 1.3], [0.9, 1.0], [1.15, 1.25], [5, 8])
    assert chart_to_bars(p) == [
        Bar(ts=1609459200000, open=1.0, high=1.2, low=0.9, close=1.15, volume=5.0),
        Bar(ts=1609459260000, open=1.1, high=1.3, low=1.0, close=1.25, volume=8.0),
    ]


def test_chart_to_bars_skips_null_closed_market_rows():
    p = _payload([1609459200, 1609459260],
                 [1.0, 1.1], [1.2, 1.3], [0.9, 1.0], [1.15, None])
    assert [b.ts for b in chart_to_bars(p)] == [1609459200000]


def test_chart_to_bars_raises_on_error_envelope():
    with pytest.raises(RuntimeError):
        chart_to_bars(_payload([], [], [], [], [], error={"code": "Not Found"}))


def test_quote_from_payload_reads_meta_price():
    assert quote_from_payload(_payload([1], [1.0], [1.0], [1.0], [1.0], price=1.5)) == 1.5
    assert quote_from_payload({"chart": {"result": []}}) is None


def test_fetch_bars_range_filters_to_window_and_converts():
    # injected fetcher returns two candles regardless of args
    def fake(symbol, interval, p1, p2):
        assert symbol == "EURUSD=X"
        return _payload([1609459200, 1609459260], [1.0, 1.1], [1.2, 1.3], [0.9, 1.0], [1.15, 1.25])

    # request only the first bar's ts -> second is filtered out
    bars = fetch_bars_range("EURUSD", "1m", 1609459200000, 1609459200000, fetch_chart=fake)
    assert [b.ts for b in bars] == [1609459200000]


def test_fetch_bars_range_chunks_long_1m_windows():
    calls = []

    def fake(symbol, interval, p1, p2):
        calls.append((p1, p2))
        return _payload([], [], [], [], [])  # empty is fine; we only count windows

    day = 86_400_000
    fetch_bars_range("EURUSD", "1m", 0, 8 * day, fetch_chart=fake)  # 8d > 7d cap -> 2 windows
    assert len(calls) == 2


def test_make_yahoo_fetch_latest_returns_zero_arg_callable():
    captured = {}

    def fake(symbol, interval, p1, p2):
        captured.update(symbol=symbol, p1=p1, p2=p2)
        return _payload([p1 + 60], [1.0], [1.0], [1.0], [1.0])  # a candle inside the window

    latest = make_yahoo_fetch_latest("eurusd", "1m", lookback=3, fetch_chart=fake)
    bars = latest()
    assert captured["symbol"] == "EURUSD=X"
    assert captured["p2"] - captured["p1"] >= 3 * 60  # window spans >= lookback intervals
    assert [b.ts for b in bars] == [(captured["p1"] + 60) * 1000]  # epoch-ms, UTC
