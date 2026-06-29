"""Task-3: Strategy.history_async() -> Future (off-thread read, call-time clamp)."""
import concurrent.futures

from vike_trader_app.core.model import Bar
from vike_trader_app.core.engine import SingleSymbolEngine
from vike_trader_app.core.compat_strategy import SingleSymbolStrategy as Strategy
from vike_trader_app.data.catalog import Catalog
from vike_trader_app.data.parquet_source import append_series


def _series(root, sym, n=10):
    append_series([Bar(ts=t * 60_000, open=1.0 + t, high=2.0 + t, low=0.5 + t, close=1.5 + t,
                       volume=10.0 + t) for t in range(n)], root, sym, "1m")


def test_async_matches_sync_and_clamp_is_call_time(tmp_path):
    _series(tmp_path, "AAA", 10)
    drv = [Bar(ts=t * 60_000, open=1, high=1, low=1, close=1) for t in range(10)]
    out = {}

    class S(Strategy):
        def on_bar(self, bar):
            if self.index == 5:
                fut = self.history_async("AAA", "1m", 100)   # capture now at bar 5 (ts=300000)
                out["fut"] = fut
                out["sync_at_5"] = self.history("AAA", "1m", 100)

    SingleSymbolEngine(drv, S(), catalog=Catalog(str(tmp_path))).run()
    assert isinstance(out["fut"], concurrent.futures.Future)
    df = out["fut"].result(timeout=10)
    # async result clamped to now-at-call (bar 5 -> ts<=300000), even though the run advanced to bar 9
    assert df["ts"].max() == 5 * 60_000
    assert df["ts"].to_list() == out["sync_at_5"]["ts"].to_list()
