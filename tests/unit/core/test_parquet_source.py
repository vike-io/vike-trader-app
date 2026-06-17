"""Parquet store round-trip tests (Phase 1, step 0)."""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import parquet_source as ps
from vike_trader_app.data.parquet_source import read_bars_parquet, write_bars_parquet


def test_parquet_roundtrip(tmp_path):
    bars = [
        Bar(ts=1, open=1.0, high=2.0, low=0.5, close=1.5, volume=3.0),
        Bar(ts=2, open=1.5, high=2.5, low=1.0, close=2.0, volume=4.0),
    ]
    path = tmp_path / "bars.parquet"
    write_bars_parquet(bars, path)
    assert read_bars_parquet(path) == bars


def test_truncate_series_before_and_after(tmp_path):
    from vike_trader_app.core.model import Bar
    root = str(tmp_path)
    bars = [Bar(ts=i * 86_400_000, open=1, high=1, low=1, close=1, volume=1.0) for i in range(1, 11)]  # ts day1..day10
    ps.append_series(bars, root, "BTCUSDT", "1m")

    # delete everything before day 4 -> removes day1,2,3
    removed = ps.truncate_series(root, "BTCUSDT", "1m", before_ts=4 * 86_400_000)
    assert removed == 3
    left = ps.read_series(root, "BTCUSDT", "1m")
    assert [b.ts for b in left] == [i * 86_400_000 for i in range(4, 11)]

    # delete everything after day 8 -> removes day9,10
    removed2 = ps.truncate_series(root, "BTCUSDT", "1m", after_ts=8 * 86_400_000)
    assert removed2 == 2
    left2 = ps.read_series(root, "BTCUSDT", "1m")
    assert [b.ts for b in left2] == [i * 86_400_000 for i in range(4, 9)]


def test_truncate_series_emptying_a_partition_unlinks_it(tmp_path):
    from vike_trader_app.core.model import Bar
    root = str(tmp_path)
    # two months: 2024-01 and 2024-02
    jan = [Bar(ts=1_704_067_200_000 + i * 86_400_000, open=1, high=1, low=1, close=1, volume=1) for i in range(5)]
    feb = [Bar(ts=1_706_745_600_000 + i * 86_400_000, open=1, high=1, low=1, close=1, volume=1) for i in range(5)]
    ps.append_series(jan + feb, root, "X", "1d")
    # delete everything before Feb -> the Jan partition should be fully removed (unlinked)
    removed = ps.truncate_series(root, "X", "1d", before_ts=1_706_745_600_000)
    assert removed == 5
    assert not (ps.series_dir(root, "X", "1d") / "2024-01.parquet").exists()
    assert (ps.series_dir(root, "X", "1d") / "2024-02.parquet").exists()


def test_truncate_series_noop_when_nothing_matches(tmp_path):
    from vike_trader_app.core.model import Bar
    root = str(tmp_path)
    ps.append_series([Bar(ts=i, open=1, high=1, low=1, close=1, volume=1) for i in range(1, 4)], root, "Y", "1m")
    assert ps.truncate_series(root, "Y", "1m", before_ts=0) == 0   # nothing before ts=0


def test_read_series_quarantines_a_corrupt_partition(tmp_path):
    """A truncated/corrupt month partition (live app killed mid-write) must degrade to a refillable
    gap, not crash the whole symbol read. Regression for the parquet-corruption robustness gap."""
    root = str(tmp_path)
    jan = [Bar(ts=1_704_067_200_000 + i * 86_400_000, open=1, high=1, low=1, close=1, volume=1) for i in range(5)]
    feb = [Bar(ts=1_706_745_600_000 + i * 86_400_000, open=1, high=1, low=1, close=1, volume=1) for i in range(5)]
    ps.append_series(jan + feb, root, "BTCUSDT", "1d")
    # Corrupt the Feb partition: overwrite it with non-parquet bytes (a partial write leaves garbage).
    feb_path = ps.series_dir(root, "BTCUSDT", "1d") / "2024-02.parquet"
    feb_path.write_bytes(b"PAR1 truncated garbage not a real parquet file")

    # Must NOT raise; returns only the readable (Jan) bars — Feb is now a refillable gap.
    left = ps.read_series(root, "BTCUSDT", "1d")
    assert [b.ts for b in left] == [b.ts for b in jan]
    # read_series_since goes through the same guard.
    since = ps.read_series_since(root, "BTCUSDT", "1d", 1_704_067_200_000)
    assert [b.ts for b in since] == [b.ts for b in jan]


def test_read_series_quarantines_a_corrupt_legacy_file(tmp_path):
    """A corrupt LEGACY single file must also degrade to empty, not crash."""
    root = str(tmp_path)
    legacy = ps.legacy_path(root, "ETHUSDT", "1h")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"not a parquet")
    assert ps.read_series(root, "ETHUSDT", "1h") == []
