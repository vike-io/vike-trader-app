from vike_trader_app.core.model import Bar
from vike_trader_app.data.parallel_read import read_series_many


class _Cat:
    """Minimal catalog stub matching Catalog.query's signature."""
    def __init__(self, data):  # data: {symbol: list[Bar]}
        self._data = data
    def query(self, symbol, interval, start=None, end=None):
        if symbol == "BAD":
            raise ValueError("boom")
        return list(self._data.get(symbol, []))


def _b(ts):
    return Bar(ts=ts, open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)


def test_parallel_matches_sequential():
    data = {"AAA": [_b(0), _b(60_000)], "BBB": [_b(0)], "CCC": []}
    cat = _Cat(data)
    syms = ["AAA", "BBB", "CCC"]
    out = read_series_many(cat, syms, "1m")
    assert out == {s: cat.query(s, "1m") for s in syms}


def test_empty_symbols_returns_empty():
    assert read_series_many(_Cat({}), [], "1m") == {}


def test_failing_symbol_isolated():
    cat = _Cat({"GOOD": [_b(0)]})
    out = read_series_many(cat, ["GOOD", "BAD"], "1m")
    assert out["BAD"] == []                 # a failing symbol degrades to [] (logged), not a raise
    assert len(out["GOOD"]) == 1
