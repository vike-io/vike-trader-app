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
from vike_trader_app.core.timeframe import resample  # noqa: E402
from vike_trader_app.data.catalog import Catalog  # noqa: E402
from vike_trader_app.data.duck_catalog import DuckCatalog  # noqa: E402
from vike_trader_app.data.parquet_source import write_bars_parquet  # noqa: E402

_HOUR = 3_600_000


def _tuples(bars):
    return [(b.ts, b.open, b.high, b.low, b.close, b.volume) for b in bars]


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


# --- resample (Phase 1: derive a timeframe straight from the Parquet base) --------------

def test_duck_resample_is_byte_identical_to_core_timeframe(tmp_path):
    base = 1_700_000_000_000
    # 120 one-minute bars (2h) with varying OHLCV so first/max/min/last/sum all matter.
    bars = [Bar(ts=base + i * 60_000, open=100 + i, high=110 + i, low=90 - i,
                close=100 + (i % 7), volume=i + 1.0) for i in range(120)]
    _seed(tmp_path, "BTCUSDT", "1m", bars)
    got = DuckCatalog(str(tmp_path)).resample("BTCUSDT", "1m", _HOUR)
    assert _tuples(got) == _tuples(resample(bars, _HOUR))


def test_duck_resample_includes_partial_final_bucket(tmp_path):
    # 90 1m bars = 1.5h -> two buckets, the second partial (30 bars) but present.
    bars = [Bar(ts=i * 60_000, open=1, high=1, low=1, close=1, volume=1.0) for i in range(90)]
    _seed(tmp_path, "X", "1m", bars)
    got = DuckCatalog(str(tmp_path)).resample("X", "1m", _HOUR)
    assert [b.ts for b in got] == [0, _HOUR]
    assert got[1].volume == 30.0


def test_duck_resample_sliced_matches_core_on_same_slice(tmp_path):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=200 + i, low=i, close=100 + i, volume=1.0)
            for i in range(180)]
    _seed(tmp_path, "X", "1m", bars)
    duck, poll = DuckCatalog(str(tmp_path)), Catalog(str(tmp_path))
    s, e = _HOUR, 2 * _HOUR + 59 * 60_000
    assert _tuples(duck.resample("X", "1m", _HOUR, s, e)) == \
           _tuples(resample(poll.query("X", "1m", s, e), _HOUR))


def test_duck_resample_nonaligned_bounds_keep_full_edge_buckets(tmp_path):
    """A mid-bucket start/end must NOT truncate the edge buckets. The old WHERE-ts pre-filter fed the
    edge buckets only the in-window base bars (partial OHLCV + a phantom bucket); HAVING keeps whole
    buckets whose START is in range, each aggregated from ALL its base bars."""
    bars = [Bar(ts=i * 60_000, open=100 + i, high=200 + i, low=i, close=100 + i, volume=1.0)
            for i in range(120)]                       # 0:00..1:59 -> buckets at 0:00 and 1:00
    _seed(tmp_path, "X", "1m", bars)
    # window 0:30..1:30 — mid-bucket on both ends
    got = DuckCatalog(str(tmp_path)).resample("X", "1m", _HOUR, 30 * 60_000, 90 * 60_000)
    full = {b.ts: b for b in resample(bars, _HOUR)}
    assert [b.ts for b in got] == [_HOUR]              # only the 1:00 bucket (0:00 starts before 0:30)
    assert got[0].volume == 60.0                       # the FULL hour, not the 30 in-window bars
    assert _tuples(got) == _tuples([full[_HOUR]])      # byte-identical to the full-base bucket


def test_duck_resample_missing_dataset_returns_empty(tmp_path):
    assert DuckCatalog(str(tmp_path)).resample("NOPE", "1m", _HOUR) == []


# --- get_or_derive (serve a timeframe from its own file, else derive from the 1m base) ---

def test_get_or_derive_prefers_own_cached_file(tmp_path):
    # 1h cached directly -> return it verbatim (distinct sentinel), not a derived series.
    _seed(tmp_path, "X", "1m", [Bar(ts=i * 60_000, open=1, high=1, low=1, close=1, volume=1.0)
                                for i in range(120)])
    _seed(tmp_path, "X", "1h", [Bar(ts=0, open=9, high=9, low=9, close=9, volume=99.0)])
    got = DuckCatalog(str(tmp_path)).get_or_derive("X", "1h")
    assert len(got) == 1 and got[0].volume == 99.0  # from the 1h file, not derived from 1m


def test_get_or_derive_falls_back_to_resampling_the_base(tmp_path):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=110 + i, low=90 - i, close=100 + (i % 5),
                volume=i + 1.0) for i in range(120)]
    _seed(tmp_path, "X", "1m", bars)  # only the 1m base is cached
    got = DuckCatalog(str(tmp_path)).get_or_derive("X", "1h")  # no 1h file -> derive from 1m
    assert _tuples(got) == _tuples(resample(bars, _HOUR))


def test_get_or_derive_base_interval_returns_base(tmp_path):
    bars = [Bar(ts=i * 60_000, open=1, high=1, low=1, close=1, volume=1.0) for i in range(5)]
    _seed(tmp_path, "X", "1m", bars)
    assert _tuples(DuckCatalog(str(tmp_path)).get_or_derive("X", "1m")) == _tuples(bars)


def test_get_or_derive_missing_everything_returns_empty(tmp_path):
    assert DuckCatalog(str(tmp_path)).get_or_derive("NOPE", "1h") == []
