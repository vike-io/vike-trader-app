"""Partitioned series I/O: append-only monthly Parquet + legacy single-file back-compat (Phase 2b).

``append_series`` writes new bars into per-month partitions, rewriting ONLY the months that
changed (the append-only win at multi-million-bar scale). ``read_series`` reads a whole series
back, merging a legacy ``<interval>.parquet`` with month partitions during/after migration.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import parquet_source as ps

NOV = 1_700_000_000_000      # 2023-11
DEC = 1_701_500_000_000      # 2023-12


def _bar(ts, close=100.0):
    return Bar(ts=ts, open=close, high=close + 1, low=close - 1, close=close, volume=1.0)


def test_append_series_writes_monthly_partitions(tmp_path):
    root = str(tmp_path)
    ps.append_series([_bar(NOV), _bar(DEC)], root, "BTCUSDT", "1m")
    d = tmp_path / "BTCUSDT" / "1m"
    assert (d / "2023-11.parquet").exists()
    assert (d / "2023-12.parquet").exists()
    assert not (tmp_path / "BTCUSDT" / "1m.parquet").exists()  # not the legacy single file


def test_read_series_round_trips_across_partitions(tmp_path):
    root = str(tmp_path)
    bars = [_bar(NOV), _bar(NOV + 60_000), _bar(DEC)]
    ps.append_series(bars, root, "BTCUSDT", "1m")
    assert [b.ts for b in ps.read_series(root, "BTCUSDT", "1m")] == [NOV, NOV + 60_000, DEC]


def test_append_series_only_rewrites_the_touched_month(tmp_path, monkeypatch):
    root = str(tmp_path)
    ps.append_series([_bar(NOV), _bar(DEC)], root, "BTCUSDT", "1m")  # two months on disk
    written: list[str] = []
    orig = ps.write_bars_parquet
    monkeypatch.setattr(ps, "write_bars_parquet",
                        lambda bars, path: (written.append(str(path)), orig(bars, path))[1])
    ps.append_series([_bar(DEC + 60_000)], root, "BTCUSDT", "1m")  # only December changes
    assert written, "expected a write"
    assert all("2023-12" in w for w in written)  # November partition was NOT rewritten


def test_append_series_dedups_within_a_month(tmp_path):
    root = str(tmp_path)
    ps.append_series([_bar(NOV, close=1.0)], root, "X", "1m")
    ps.append_series([_bar(NOV, close=9.0)], root, "X", "1m")  # same ts -> replace
    out = ps.read_series(root, "X", "1m")
    assert len(out) == 1 and out[0].close == 9.0


def test_read_series_back_compat_reads_legacy_single_file(tmp_path):
    # A pre-Phase-2b cache wrote one file; read_series must still see it.
    root = str(tmp_path)
    legacy = tmp_path / "ETHUSDT" / "1m.parquet"
    legacy.parent.mkdir(parents=True)
    ps.write_bars_parquet([_bar(NOV), _bar(DEC)], legacy)
    assert [b.ts for b in ps.read_series(root, "ETHUSDT", "1m")] == [NOV, DEC]


def test_append_series_migrates_legacy_then_appends(tmp_path):
    root = str(tmp_path)
    legacy = tmp_path / "ETHUSDT" / "1m.parquet"
    legacy.parent.mkdir(parents=True)
    ps.write_bars_parquet([_bar(NOV)], legacy)
    ps.append_series([_bar(DEC)], root, "ETHUSDT", "1m")  # triggers migration of the legacy file
    assert not legacy.exists()                            # legacy split into partitions + removed
    d = tmp_path / "ETHUSDT" / "1m"
    assert (d / "2023-11.parquet").exists() and (d / "2023-12.parquet").exists()
    assert [b.ts for b in ps.read_series(root, "ETHUSDT", "1m")] == [NOV, DEC]


def test_read_series_missing_returns_empty(tmp_path):
    assert ps.read_series(str(tmp_path), "NOPE", "1m") == []


# --- read_series_since: partition-pruned tail read (for incremental rollup refresh) ---

def test_read_series_since_prunes_older_month_partitions(tmp_path, monkeypatch):
    root = str(tmp_path)
    ps.append_series([_bar(NOV)], root, "X", "1m")  # 2023-11 partition
    ps.append_series([_bar(DEC)], root, "X", "1m")  # 2023-12 partition
    reads: list[str] = []
    orig = ps.read_bars_parquet
    monkeypatch.setattr(ps, "read_bars_parquet",
                        lambda p: (reads.append(str(p)), orig(p))[1])
    out = ps.read_series_since(root, "X", "1m", DEC)
    assert [b.ts for b in out] == [DEC]
    assert all("2023-11" not in r for r in reads)  # the older month partition was not read


def test_read_series_since_filters_within_the_boundary_month(tmp_path):
    root = str(tmp_path)
    ps.append_series([_bar(DEC), _bar(DEC + 60_000)], root, "X", "1m")  # both in 2023-12
    out = ps.read_series_since(root, "X", "1m", DEC + 60_000)
    assert [b.ts for b in out] == [DEC + 60_000]  # boundary-month partition read, then filtered


def test_read_series_since_missing_returns_empty(tmp_path):
    assert ps.read_series_since(str(tmp_path), "NOPE", "1m", 0) == []
