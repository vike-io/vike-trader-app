"""Crypto-breadth providers — pure transforms only (symbol mapping, interval maps, candle parse).

Network/pagination wrappers hit public REST and are exercised by scripts, not here (mirrors the
Binance convention). Each exchange returns a different candle shape / order, so ``to_bars``
normalises every one to ascending UTC ``Bar``s.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import (
    bybit_source as bybit,
    coinbase_source as coinbase,
    kraken_source as kraken,
    okx_source as okx,
)


# --- Bybit: result.list = [startMs, o, h, l, c, vol, turnover], DESCENDING -----------------

def test_bybit_to_bars_sorts_ascending():
    raw = [["1700000060000", "2", "3", "1", "2.5", "10", "x"],
           ["1700000000000", "1", "2", "0.5", "1.5", "9", "x"]]
    assert bybit.to_bars(raw) == [
        Bar(1700000000000, 1, 2, 0.5, 1.5, 9.0),
        Bar(1700000060000, 2, 3, 1, 2.5, 10.0),
    ]


def test_bybit_symbol_and_interval():
    assert bybit.market_symbol("btcusdt") == "BTCUSDT"
    assert bybit.INTERVALS["1h"] == "60" and bybit.INTERVALS["1d"] == "D"


# --- OKX: data = [ts, o, h, l, c, vol, ...], DESCENDING; instId is dashed ------------------

def test_okx_to_bars_sorts_ascending():
    raw = [["1700000060000", "2", "3", "1", "2.5", "10", "0", "0", "1"],
           ["1700000000000", "1", "2", "0.5", "1.5", "9", "0", "0", "1"]]
    assert okx.to_bars(raw) == [
        Bar(1700000000000, 1, 2, 0.5, 1.5, 9.0),
        Bar(1700000060000, 2, 3, 1, 2.5, 10.0),
    ]


def test_okx_symbol_is_dashed():
    assert okx.market_symbol("BTCUSDT") == "BTC-USDT"
    assert okx.market_symbol("ETHUSD") == "ETH-USD"
    assert okx.INTERVALS["1h"] == "1H" and okx.INTERVALS["1d"] == "1D"


# --- Coinbase: [time_s, low, high, open, close, vol], DESCENDING; product dashed -----------

def test_coinbase_to_bars_maps_odd_column_order():
    raw = [[1700000060, 1, 3, 2, 2.5, 10],     # [time, LOW, HIGH, OPEN, CLOSE, vol]
           [1700000000, 0.5, 2, 1, 1.5, 9]]
    assert coinbase.to_bars(raw) == [
        Bar(1700000000000, 1, 2, 0.5, 1.5, 9.0),
        Bar(1700000060000, 2, 3, 1, 2.5, 10.0),
    ]


def test_coinbase_symbol_and_granularity_seconds():
    assert coinbase.market_symbol("BTCUSD") == "BTC-USD"
    assert coinbase.INTERVALS["1m"] == 60 and coinbase.INTERVALS["1d"] == 86400
    assert "3m" not in coinbase.INTERVALS  # Coinbase has no 3m granularity


# --- Kraken: result[pair] = [time_s, o, h, l, c, vwap, vol, count], ASCENDING --------------

def test_kraken_to_bars_uses_volume_column_six():
    raw = [[1700000000, "1", "2", "0.5", "1.5", "1.2", "9", 5],
           [1700000060, "2", "3", "1", "2.5", "2.1", "10", 6]]
    assert kraken.to_bars(raw) == [
        Bar(1700000000000, 1, 2, 0.5, 1.5, 9.0),
        Bar(1700000060000, 2, 3, 1, 2.5, 10.0),
    ]


def test_kraken_response_picks_pair_key_not_last():
    resp = {"error": [], "result": {"XXBTZUSD": [[1700000000, "1", "2", "0.5", "1.5", "1.2", "9", 5]],
                                     "last": 1700000000}}
    bars = kraken.parse_response(resp)
    assert bars == [Bar(1700000000000, 1, 2, 0.5, 1.5, 9.0)]


def test_kraken_symbol_maps_btc_to_xbt():
    assert kraken.market_symbol("BTCUSDT") == "XBTUSDT"
    assert kraken.market_symbol("ETHUSD") == "ETHUSD"
    assert kraken.INTERVALS["1h"] == 60 and kraken.INTERVALS["1d"] == 1440
