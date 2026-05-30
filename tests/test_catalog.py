"""Queryable Parquet data catalog over the local cache layout."""

from pathlib import Path

from vike_trader_app.core.model import Bar
from vike_trader_app.data.catalog import Catalog
from vike_trader_app.data.parquet_source import write_bars_parquet


def _bars(n, base_ts=0):
    return [Bar(ts=base_ts + i * 60_000, open=100, high=101, low=99, close=100 + i, volume=1.0) for i in range(n)]


def _seed(root: Path, symbol: str, interval: str, bars):
    p = root / symbol / f"{interval}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_bars_parquet(bars, p)


def test_catalog_lists_symbols_and_intervals(tmp_path):
    _seed(tmp_path, "BTCUSDT", "1m", _bars(10))
    _seed(tmp_path, "BTCUSDT", "1h", _bars(5))
    _seed(tmp_path, "ETHUSDT", "1m", _bars(7))
    cat = Catalog(str(tmp_path))
    assert cat.symbols() == ["BTCUSDT", "ETHUSDT"]
    assert cat.intervals("BTCUSDT") == ["1h", "1m"]


def test_catalog_dataset_info_reports_range_and_count(tmp_path):
    _seed(tmp_path, "BTCUSDT", "1m", _bars(10, base_ts=1_000))
    cat = Catalog(str(tmp_path))
    ds = cat.info("BTCUSDT", "1m")
    assert ds.n_bars == 10
    assert ds.start_ts == 1_000
    assert ds.end_ts == 1_000 + 9 * 60_000
    assert len(cat.list_datasets()) == 1


def test_catalog_query_slices_inclusive(tmp_path):
    _seed(tmp_path, "BTCUSDT", "1m", _bars(10))
    cat = Catalog(str(tmp_path))
    out = cat.query("BTCUSDT", "1m", start=60_000, end=180_000)
    assert [b.ts for b in out] == [60_000, 120_000, 180_000]


def test_catalog_query_missing_dataset_returns_empty(tmp_path):
    cat = Catalog(str(tmp_path))
    assert cat.query("NOPE", "1m") == []
    assert cat.info("NOPE", "1m") is None
