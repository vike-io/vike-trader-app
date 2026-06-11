"""Unit tests for the cache-first bar-loading seam (ui/dataload.py). No Qt, no network."""

import time

import pytest

import vike_trader_app.data.catalog as cat_mod
import vike_trader_app.ui.dataload as dl
from vike_trader_app.core.model import Bar


def _bar(ts, px=100.0):
    return Bar(ts=ts, open=px, high=px + 1, low=px - 1, close=px)


NOW = int(time.time() * 1000)


class _Cat:
    """Catalog stub returning a canned series."""

    bars: list = []

    def query(self, *a, **k):
        return list(self.bars)


@pytest.fixture(autouse=True)
def _patch_catalog(monkeypatch):
    _Cat.bars = []
    monkeypatch.setattr(cat_mod, "Catalog", _Cat)


def test_fresh_cache_skips_network(monkeypatch):
    _Cat.bars = [_bar(NOW - i * 60_000) for i in range(50, 0, -1)]  # newest ~1min old

    def _boom(*a, **k):
        raise AssertionError("network fetch despite fresh cache")

    monkeypatch.setattr(dl, "get_bars", _boom)
    res = dl.load_symbol_bars("BTCUSDT", "1m", NOW)
    assert res.ok and len(res.bars) == 50
    assert not res.stale_fallback and res.error == ""


def test_stale_cache_tops_up_over_network(monkeypatch):
    _Cat.bars = [_bar(NOW - 3 * 3_600_000)]  # hours old -> stale
    fetched = [_bar(NOW - i * 60_000) for i in range(10, 0, -1)]
    monkeypatch.setattr(dl, "get_bars", lambda *a, **k: fetched)
    res = dl.load_symbol_bars("BTCUSDT", "1m", NOW)
    assert res.bars == fetched
    assert not res.stale_fallback


def test_network_off_serves_cache_only(monkeypatch):
    _Cat.bars = [_bar(NOW - 3 * 3_600_000)]  # stale, but network is off

    def _boom(*a, **k):
        raise AssertionError("network fetch with network=False")

    monkeypatch.setattr(dl, "get_bars", _boom)
    res = dl.load_symbol_bars("BTCUSDT", "1m", NOW, network=False)
    assert len(res.bars) == 1  # the stale tail, no fetch


def test_fetch_failure_falls_back_to_cache(monkeypatch):
    _Cat.bars = [_bar(NOW - 3 * 3_600_000)]

    def _fail(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(dl, "get_bars", _fail)
    res = dl.load_symbol_bars("BTCUSDT", "1m", NOW)
    assert res.ok and res.stale_fallback
    assert "offline" in res.error


def test_fetch_failure_with_empty_cache_is_error(monkeypatch):
    def _fail(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(dl, "get_bars", _fail)
    res = dl.load_symbol_bars("BTCUSDT", "1m", NOW)
    assert not res.ok and res.bars == []
    assert not res.stale_fallback
    assert "offline" in res.error


def test_lookback_window_per_interval(monkeypatch):
    """The query start honors the per-interval lookback table (1d -> 5y of dailies)."""
    seen = {}

    class _SpyCat(_Cat):
        def query(self, symbol, interval, start, end):
            seen["window_days"] = (end - start) / 86_400_000
            return []

    monkeypatch.setattr(cat_mod, "Catalog", _SpyCat)
    monkeypatch.setattr(dl, "get_bars", lambda *a, **k: [])
    dl.load_symbol_bars("BTCUSDT", "1d", NOW)
    assert seen["window_days"] == dl.INTERVAL_LOOKBACK["1d"]
