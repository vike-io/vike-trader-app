import threading

import pytest

from vike_trader_app.core.model import Bar
from vike_trader_app.data import parquet_source as ps
from vike_trader_app.data.parquet_source import (
    append_series, read_bars_parquet, read_series, write_bars_parquet,
)


def _bars(n=100):
    return [Bar(ts=i * 60_000, open=1.0 + i, high=2.0 + i, low=0.5 + i,
                close=1.5 + i, volume=10.0 + i) for i in range(n)]


def test_read_primitive_round_trip(tmp_path):
    p = tmp_path / "x.parquet"
    bars = _bars(50)
    write_bars_parquet(bars, p)
    got = read_bars_parquet(p)
    assert got == bars                      # DuckDB-backed read == written bars (parity)


def test_quarantine_corrupt_file_returns_empty(tmp_path, caplog):
    p = tmp_path / "corrupt.parquet"
    p.write_bytes(b"not a parquet file at all")
    assert ps._read_partition(p) == []      # quarantined, not raised
    assert any("unreadable parquet partition" in r.message for r in caplog.records)


def test_duck_is_the_active_backend():
    # In the dev/app environment [duck] is installed; the thread-safe path must be active.
    assert ps._HAS_DUCK is True


def test_concurrent_reads_no_crash(tmp_path):
    root = str(tmp_path)
    syms = ["AAA", "BBB", "CCC"]
    bars = _bars(100)
    for s in syms:
        append_series(bars, root, s, "1m")
    errors: list[Exception] = []

    def worker():
        try:
            for _ in range(40):
                for s in syms:
                    got = read_series(root, s, "1m")
                    assert len(got) == 100 and got[0].ts == 0
        except Exception as e:                # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors                         # 12 threads × 40 rounds of concurrent reads: no segfault, correct bars
