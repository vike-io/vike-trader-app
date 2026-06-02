"""Phase-0 benchmark: DuckDB vs Polars for catalog metadata, full read, and 1m->1h resample.

    python scripts/bench_duckdb_phase0.py [parquet-root] [symbol] [interval]

Defaults to the real BTCUSDT 1m cache. Reports median ms over a few runs and checks the DuckDB
resample is identical to ``core.timeframe.resample`` (pre-validates Phase 1). Read-only.
"""

import statistics
import sys
import time
from pathlib import Path

from vike_trader_app.core.timeframe import resample
from vike_trader_app.data.catalog import Catalog
from vike_trader_app.data.duck_catalog import DuckCatalog

_HOUR_MS = 3_600_000


def _median_ms(fn, runs: int = 5) -> float:
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return statistics.median(samples)


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "C:/Projects/vike-trader-app/storage/parquet"
    symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
    interval = sys.argv[3] if len(sys.argv) > 3 else "1m"
    path = Path(root) / symbol / f"{interval}.parquet"
    if not path.exists():
        print(f"no data at {path}")
        return

    poll, duck = Catalog(root), DuckCatalog(root)
    n = poll.info(symbol, interval).n_bars
    print(f"file: {path}  ({path.stat().st_size / 1e6:.2f} MB, {n:,} bars)\n")

    p_info = _median_ms(lambda: poll.info(symbol, interval))
    d_info = _median_ms(lambda: duck.info(symbol, interval))
    print(f"info()        polars {p_info:8.2f} ms   duckdb {d_info:8.2f} ms   {p_info / d_info:6.1f}x")

    p_read = _median_ms(lambda: poll.query(symbol, interval))
    d_read = _median_ms(lambda: duck.query(symbol, interval))
    print(f"read all      polars {p_read:8.2f} ms   duckdb {d_read:8.2f} ms   {p_read / d_read:6.1f}x")

    bars = poll.query(symbol, interval)

    def duck_resample():
        return duck._con.execute(
            "SELECT (ts - ts % ?) AS b, arg_min(open, ts), max(high), min(low), "
            "arg_max(close, ts), sum(volume) FROM read_parquet(?) GROUP BY b ORDER BY b",
            [_HOUR_MS, path.as_posix()],
        ).fetchall()

    p_rs = _median_ms(lambda: resample(bars, _HOUR_MS))
    d_rs = _median_ms(duck_resample)
    # End-to-end "1m file -> 1h bars": Polars must read all bars to Python first, then resample.
    print(f"1m->1h (e2e)  polars {p_read + p_rs:8.2f} ms   duckdb {d_rs:8.2f} ms   "
          f"{(p_read + p_rs) / d_rs:6.1f}x")

    py, dk = resample(bars, _HOUR_MS), duck_resample()
    match = len(py) == len(dk) and all(
        b.ts == int(r[0]) and abs(b.open - r[1]) < 1e-9 and abs(b.high - r[2]) < 1e-9
        and abs(b.low - r[3]) < 1e-9 and abs(b.close - r[4]) < 1e-9 and abs(b.volume - r[5]) < 1e-9
        for b, r in zip(py, dk)
    )
    print(f"\nresample parity (duckdb == core.timeframe.resample): {match}  ({len(py)} 1h bars)")


if __name__ == "__main__":
    main()
