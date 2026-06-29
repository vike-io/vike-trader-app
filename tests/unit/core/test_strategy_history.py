"""Tests for Strategy.history() — look-ahead-safe cache read (Task 2, slice C)."""
from datetime import timedelta

import polars as pl

from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.data.catalog import Catalog
from vike_trader_app.data.parquet_source import append_series


def _series(root, sym, n=10):
    bars = [Bar(ts=t * 60_000, open=1.0 + t, high=2.0 + t, low=0.5 + t, close=1.5 + t, volume=10.0 + t)
            for t in range(n)]
    append_series(bars, root, sym, "1m")
    return bars


def _engine(bars, strat, root):
    return SingleSymbolEngine(bars, strat, catalog=Catalog(str(root)))


def test_count_period_range_forms(tmp_path):
    root = tmp_path
    _series(root, "AAA", 10)
    drv = [Bar(ts=9 * 60_000, open=1, high=1, low=1, close=1)]   # now = ts of bar 9

    captured = {}

    class S(Strategy):
        def on_bar(self, bar):
            captured["count"] = self.history("AAA", "1m", 3)
            captured["period"] = self.history("AAA", "1m", period=timedelta(minutes=5))
            captured["range"] = self.history("AAA", "1m", start=0, end=4 * 60_000)

    _engine(drv, S(), root).run()
    assert captured["count"].height == 3                       # last 3 bars <= now
    assert captured["count"]["ts"].to_list() == [7 * 60_000, 8 * 60_000, 9 * 60_000]
    assert captured["range"]["ts"].max() == 4 * 60_000         # explicit range respected
    assert set(captured["count"].columns) == {"ts", "open", "high", "low", "close", "volume"}


def test_look_ahead_clamp(tmp_path):
    root = tmp_path
    _series(root, "AAA", 10)
    bars = [Bar(ts=t * 60_000, open=1, high=1, low=1, close=1) for t in range(5)]   # drive bars 0..4
    maxes = []

    class S(Strategy):
        def on_bar(self, bar):
            df = self.history("AAA", "1m", 100)        # ask for everything
            maxes.append(df["ts"].max())

    _engine(bars, S(), root).run()
    # at each bar i (ts=i*60_000), history must never return a ts in the future
    assert maxes == [i * 60_000 for i in range(5)]


def test_multi_symbol_has_symbol_column(tmp_path):
    root = tmp_path
    _series(root, "AAA", 10); _series(root, "BBB", 10)
    drv = [Bar(ts=9 * 60_000, open=1, high=1, low=1, close=1)]
    out = {}

    class S(Strategy):
        def on_bar(self, bar):
            out["df"] = self.history(["AAA", "BBB"], "1m", 3)

    _engine(drv, S(), root).run()
    df = out["df"]
    assert "symbol" in df.columns and set(df["symbol"].unique().to_list()) == {"AAA", "BBB"}


def test_uncached_symbol_is_empty(tmp_path):
    root = tmp_path
    _series(root, "AAA", 5)
    drv = [Bar(ts=4 * 60_000, open=1, high=1, low=1, close=1)]
    out = {}

    class S(Strategy):
        def on_bar(self, bar):
            out["df"] = self.history("NOPE", "1m", 10)

    _engine(drv, S(), root).run()
    assert out["df"].height == 0
    assert set(out["df"].columns) == {"ts", "open", "high", "low", "close", "volume"}


def test_bad_args_raise(tmp_path):
    root = tmp_path; _series(root, "AAA", 3)
    drv = [Bar(ts=0, open=1, high=1, low=1, close=1)]

    class S(Strategy):
        def on_bar(self, bar):
            try:
                self.history("AAA", "1m")          # none of count/period/range
            except ValueError:
                out["ok"] = True

    out = {}
    _engine(drv, S(), root).run()
    assert out.get("ok")


def test_multi_symbol_mixed_cached_uncached(tmp_path):
    root = tmp_path
    _series(root, "AAA", 10)          # cached; BBB intentionally NOT cached
    drv = [Bar(ts=9 * 60_000, open=1, high=1, low=1, close=1)]
    out = {}

    class S(Strategy):
        def on_bar(self, bar):
            out["df"] = self.history(["AAA", "BBB"], "1m", 3)

    _engine(drv, S(), root).run()
    df = out["df"]
    assert "symbol" in df.columns
    assert df.filter(pl.col("symbol") == "AAA").height == 3   # cached -> last 3
    assert df.filter(pl.col("symbol") == "BBB").height == 0   # uncached -> empty, no crash
