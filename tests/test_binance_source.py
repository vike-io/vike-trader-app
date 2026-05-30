"""Binance data-source tests (Phase 1, step 0). Pure transform — no network."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.binance_source import klines_to_bars


def test_klines_to_bars_parses_ohlcv():
    # Binance kline shape: [openTime, open, high, low, close, volume, closeTime, ...]
    raw = [
        [
            1609459200000,
            "29000.00",
            "29100.00",
            "28950.00",
            "29050.00",
            "10.5",
            1609459259999,
            "x",
            100,
            "x",
            "x",
            "0",
        ],
        [
            1609459260000,
            "29050.00",
            "29080.00",
            "29000.00",
            "29010.00",
            "8.2",
            1609459319999,
            "x",
            90,
            "x",
            "x",
            "0",
        ],
    ]
    assert klines_to_bars(raw) == [
        Bar(ts=1609459200000, open=29000.0, high=29100.0, low=28950.0, close=29050.0, volume=10.5),
        Bar(ts=1609459260000, open=29050.0, high=29080.0, low=29000.0, close=29010.0, volume=8.2),
    ]
