"""DuckCatalog: read-only DuckDB-backed catalog over the existing Parquet cache (Phase 0).

Must reproduce ``Catalog``'s behaviour exactly — symbols / intervals / info / query —
reading the same ``<root>/<symbol>/<interval>.parquet`` files via DuckDB instead of
loading every bar into Python. Skips when duckdb isn't installed (optional ``[duck]`` extra),
matching the importorskip convention used for the PySide6 GUI tests.
"""

from pathlib import Path

import pytest

pytest.importorskip("duckdb")

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.data.catalog import Catalog  # noqa: E402
from vike_trader_app.data.duck_catalog import DuckCatalog  # noqa: E402
from vike_trader_app.data.parquet_source import write_bars_parquet  # noqa: E402


def _bars(n, base_ts=0):
    return [Bar(ts=base_ts + i * 60_000, open=100, high=101, low=99, close=100 + i, volume=1.0)
            for i in range(n)]


def _seed(root: Path, symbol: str, interval: str, bars):
    p = root / symbol / f"{interval}.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_bars_parquet(bars, p)


def test_duck_catalog_lists_symbols_and_intervals(tmp_path):
    _seed(tmp_path, "BTCUSDT", "1m", _bars(10))
    _seed(tmp_path, "BTCUSDT", "1h", _bars(5))
    _seed(tmp_path, "ETHUSDT", "1m", _bars(7))
    cat = DuckCatalog(str(tmp_path))
    assert cat.symbols() == ["BTCUSDT", "ETHUSDT"]
    assert cat.intervals("BTCUSDT") == ["1h", "1m"]


def test_duck_catalog_info_reports_range_and_count(tmp_path):
    _seed(tmp_path, "BTCUSDT", "1m", _bars(10, base_ts=1_000))
    cat = DuckCatalog(str(tmp_path))
    ds = cat.info("BTCUSDT", "1m")
    assert ds.n_bars == 10
    assert ds.start_ts == 1_000
    assert ds.end_ts == 1_000 + 9 * 60_000
    assert len(cat.list_datasets()) == 1


def test_duck_catalog_query_slices_inclusive(tmp_path):
    _seed(tmp_path, "BTCUSDT", "1m", _bars(10))
    cat = DuckCatalog(str(tmp_path))
    out = cat.query("BTCUSDT", "1m", start=60_000, end=180_000)
    assert [b.ts for b in out] == [60_000, 120_000, 180_000]


def test_duck_catalog_missing_returns_empty(tmp_path):
    cat = DuckCatalog(str(tmp_path))
    assert cat.query("NOPE", "1m") == []
    assert cat.info("NOPE", "1m") is None


def test_duck_catalog_matches_polars_catalog(tmp_path):
    # Parity: same files, identical answers to the Polars-backed Catalog.
    base = 1_700_000_000_000
    _seed(tmp_path, "BTCUSDT", "1m", _bars(50, base_ts=base))
    duck, poll = DuckCatalog(str(tmp_path)), Catalog(str(tmp_path))
    assert duck.symbols() == poll.symbols()
    assert duck.intervals("BTCUSDT") == poll.intervals("BTCUSDT")

    d_full, p_full = duck.query("BTCUSDT", "1m"), poll.query("BTCUSDT", "1m")
    assert [b.ts for b in d_full] == [b.ts for b in p_full]
    assert [b.close for b in d_full] == [b.close for b in p_full]

    s, e = base + 10 * 60_000, base + 20 * 60_000
    assert [b.ts for b in duck.query("BTCUSDT", "1m", s, e)] == \
           [b.ts for b in poll.query("BTCUSDT", "1m", s, e)]

    di, pi = duck.info("BTCUSDT", "1m"), poll.info("BTCUSDT", "1m")
    assert (di.n_bars, di.start_ts, di.end_ts) == (pi.n_bars, pi.start_ts, pi.end_ts)
