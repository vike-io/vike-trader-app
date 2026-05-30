"""Parquet store round-trip tests (Phase 1, step 0)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data.parquet_source import read_bars_parquet, write_bars_parquet


def test_parquet_roundtrip(tmp_path):
    bars = [
        Bar(ts=1, open=1.0, high=2.0, low=0.5, close=1.5, volume=3.0),
        Bar(ts=2, open=1.5, high=2.5, low=1.0, close=2.0, volume=4.0),
    ]
    path = tmp_path / "bars.parquet"
    write_bars_parquet(bars, path)
    assert read_bars_parquet(path) == bars
