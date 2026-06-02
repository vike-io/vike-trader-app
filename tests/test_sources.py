"""Source selector tests — pure routing + the forex history stitch (no network)."""

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.data.sources import (
    CRYPTO,
    FOREX,
    forex_fetch_bars_range,
    is_forex_symbol,
    select_source,
    split_range,
)

DAY = 86_400_000


def _bar(ts):
    return Bar(ts=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=0.0)


# --- symbol classification ---

def test_is_forex_symbol_distinguishes_forex_from_crypto():
    assert is_forex_symbol("EURUSD")
    assert is_forex_symbol("eurusd")  # case-insensitive
    assert is_forex_symbol("USDJPY")
    assert is_forex_symbol("EURGBP")  # cross
    assert not is_forex_symbol("EURUSDT")  # 7 chars -> crypto stablecoin pair
    assert not is_forex_symbol("BTCUSDT")
    assert not is_forex_symbol("XAUUSD")   # XAU not a fiat currency code


def test_select_source_routes_by_symbol_and_honors_override():
    assert select_source("EURUSD") is FOREX
    assert select_source("BTCUSDT") is CRYPTO
    assert select_source("EURUSD", provider="yahoo").name == "yahoo"
    assert select_source("BTCUSDT", provider="forex") is FOREX  # explicit wins
    with pytest.raises(ValueError):
        select_source("EURUSD", provider="nope")


def test_source_capabilities():
    assert FOREX.supports_live_ws is False  # forex has no push feed -> always polls
    assert CRYPTO.supports_live_ws is True


def test_crypto_breadth_providers_are_registered():
    from vike_trader_app.data.sources import CRYPTO_PROVIDERS

    for name in ("bybit", "okx", "coinbase", "kraken"):
        src = select_source("BTCUSDT", provider=name)
        assert src.name == name
        assert src.supports_live_ws is False           # REST-poll only, no push
        assert callable(src.fetch_bars_range)
        assert callable(src.make_fetch_latest("BTCUSDT", "1m"))  # builds a recent-bar poller
    assert set(("binance", "bybit", "okx", "coinbase", "kraken")) <= set(CRYPTO_PROVIDERS)


# --- range split (Dukascopy old | Yahoo recent) ---

def test_split_range_all_recent_goes_to_yahoo():
    now = 100 * DAY
    assert split_range(90 * DAY, now, now, max_age_ms=28 * DAY) == (None, (90 * DAY, now))


def test_split_range_all_old_goes_to_dukascopy():
    now = 100 * DAY
    assert split_range(10 * DAY, 20 * DAY, now, max_age_ms=28 * DAY) == ((10 * DAY, 20 * DAY), None)


def test_split_range_straddle_splits_at_cutoff():
    now = 100 * DAY
    cutoff = now - 28 * DAY
    old, recent = split_range(10 * DAY, now, now, max_age_ms=28 * DAY)
    assert old == (10 * DAY, cutoff - 1)
    assert recent == (cutoff, now)


# --- stitched forex history ---

def test_forex_fetch_bars_range_routes_and_merges():
    now = 100 * DAY
    cutoff = now - 28 * DAY
    calls = {}

    def fake_duka(sym, interval, s, e, progress=None):
        calls["duka"] = (s, e)
        return [_bar(10 * DAY), _bar(50 * DAY)]

    def fake_yahoo(sym, interval, s, e, progress=None):
        calls["yahoo"] = (s, e)
        return [_bar(cutoff), _bar(now)]

    bars = forex_fetch_bars_range("EURUSD", "1m", 10 * DAY, now,
                                  now_ms=now, yahoo_fetch=fake_yahoo, duka_fetch=fake_duka)
    assert [b.ts for b in bars] == [10 * DAY, 50 * DAY, cutoff, now]  # merged, sorted
    assert calls["duka"] == (10 * DAY, cutoff - 1)   # old -> Dukascopy
    assert calls["yahoo"] == (cutoff, now)           # recent -> Yahoo


def test_forex_fetch_bars_range_recent_only_skips_dukascopy():
    now = 100 * DAY
    called = {"duka": False}

    def fake_duka(*a, **k):
        called["duka"] = True
        return []

    def fake_yahoo(sym, interval, s, e, progress=None):
        return [_bar(99 * DAY)]

    bars = forex_fetch_bars_range("EURUSD", "1m", 95 * DAY, now,
                                  now_ms=now, yahoo_fetch=fake_yahoo, duka_fetch=fake_duka)
    assert [b.ts for b in bars] == [99 * DAY]
    assert called["duka"] is False  # nothing old -> Dukascopy not hit
