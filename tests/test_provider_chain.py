"""Provider fallback chain — try each provider in order, use the first that returns data."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import provider_chain as pc


def _bar(ts):
    return Bar(ts, 1, 1, 1, 1, 1.0)


def _fake_select(results, calls):
    """Build a ``select(symbol, provider=...)`` whose source returns ``results[provider]``."""
    class _Src:
        def __init__(self, name):
            self.name = name

        def fetch_bars_range(self, symbol, interval, start, end, progress=None):
            calls.append(self.name)
            out = results.get(self.name)
            if isinstance(out, Exception):
                raise out
            return out or []

    return lambda symbol, provider=None: _Src(provider)


def test_chain_returns_first_provider_with_data():
    calls = []
    select = _fake_select({"binance": [], "bybit": [_bar(1)]}, calls)
    bars, used = pc.fetch_chain(["binance", "bybit", "okx"], "BTCUSDT", "1m", 0, 9, select=select)
    assert [b.ts for b in bars] == [1] and used == "bybit"
    assert calls == ["binance", "bybit"]  # stops at first hit, never tries okx


def test_chain_skips_provider_that_raises():
    calls = []
    select = _fake_select({"binance": RuntimeError("boom"), "okx": [_bar(2)]}, calls)
    bars, used = pc.fetch_chain(["binance", "okx"], "BTCUSDT", "1m", 0, 9, select=select)
    assert used == "okx" and [b.ts for b in bars] == [2]


def test_chain_all_empty_returns_none_provider():
    select = _fake_select({"binance": [], "okx": []}, [])
    bars, used = pc.fetch_chain(["binance", "okx"], "BTCUSDT", "1m", 0, 9, select=select)
    assert bars == [] and used is None
