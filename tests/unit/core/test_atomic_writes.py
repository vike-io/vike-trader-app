# tests/unit/core/test_atomic_writes.py
from pathlib import Path

from vike_trader_app.core.model import Bar
from vike_trader_app.data.parquet_source import (
    append_series, read_bars_parquet, read_series, write_bars_parquet,
)


def _bars(n=5, base=0):
    return [Bar(ts=(base + i) * 60_000, open=1.0 + i, high=2.0 + i, low=0.5 + i,
                close=1.5 + i, volume=10.0 + i) for i in range(n)]


def test_write_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "x.parquet"
    write_bars_parquet(_bars(5), p)
    assert read_bars_parquet(p) == _bars(5)            # valid file
    assert not (tmp_path / "x.parquet.tmp").exists()   # tmp cleaned up by replace


def test_reader_ignores_tmp_partition(tmp_path):
    root = str(tmp_path)
    append_series(_bars(5), root, "AAA", "1m")
    # a stray .tmp (simulating a crash mid-write) must NOT be picked up by the *.parquet glob
    d = tmp_path / "AAA" / "1m"
    stray = next(d.glob("*.parquet"))
    (d / (stray.name + ".tmp")).write_bytes(b"garbage")
    assert len(read_series(root, "AAA", "1m")) == 5    # stray .tmp ignored, no crash


def test_truncate_still_works_atomically(tmp_path):
    root = str(tmp_path)
    append_series(_bars(10), root, "AAA", "1m")
    from vike_trader_app.data.parquet_source import truncate_series
    removed = truncate_series(root, "AAA", "1m", before_ts=3 * 60_000)
    assert removed == 3
    kept = read_series(root, "AAA", "1m")
    assert [b.ts for b in kept] == [i * 60_000 for i in range(3, 10)]
    assert not list((tmp_path / "AAA" / "1m").glob("*.tmp"))   # no leftover tmp
