"""Qt-free helpers for the Data Manager panel: format a catalog row + on-disk size + delete.

Mirrors the watchlist_data / chartdata convention — all display formatting and storage math is
pure (or thin file I/O) so it's unit-testable away from the Qt widget.
"""

from vike_trader_app.core.model import Bar
from vike_trader_app.data import parquet_source as ps
from vike_trader_app.data.catalog import DatasetInfo
from vike_trader_app.ui.datamanager_data import human_size, human_ts, quality_summary, row_cells

_MIN = 60_000


def _b(ts, o=100.0, h=101.0, low=99.0, c=100.0):
    return Bar(ts=ts, open=o, high=h, low=low, close=c, volume=1.0)


def _bar(ts):
    return Bar(ts=ts, open=1, high=1, low=1, close=1, volume=1.0)


def test_human_size_scales_units():
    assert human_size(0) == "0 B"
    assert human_size(512) == "512 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024 * 1024) == "5.0 MB"
    assert human_size(3 * 1024 ** 3) == "3.0 GB"


def test_human_ts_is_utc_minute():
    assert human_ts(0) == "1970-01-01 00:00"
    assert human_ts(1_700_000_000_000) == "2023-11-14 22:13"


def test_row_cells_formats_a_dataset_row():
    info = DatasetInfo("BTCUSDT", "1m", 48156, 1_700_000_000_000, 1_700_086_400_000, "p")
    cells = row_cells(info, pinned=True, size_bytes=1_572_864)
    assert cells == ["BTCUSDT", "1m", "48,156", "2023-11-14 22:13", "2023-11-15 22:13",
                     "1.5 MB", "📌"]


def test_row_cells_unpinned_has_blank_pin():
    info = DatasetInfo("ETHUSDT", "1h", 10, 0, 3_600_000, "p")
    assert row_cells(info, pinned=False, size_bytes=0)[-1] == ""


# --- series_size_bytes + delete_series (thin storage I/O) ---

def test_series_size_bytes_sums_partitions(tmp_path):
    from vike_trader_app.ui.datamanager_data import series_size_bytes

    ps.append_series([_bar(1_700_000_000_000)], str(tmp_path), "X", "1m")  # one month partition
    assert series_size_bytes(str(tmp_path), "X", "1m") > 0


def test_series_size_bytes_zero_when_absent(tmp_path):
    from vike_trader_app.ui.datamanager_data import series_size_bytes

    assert series_size_bytes(str(tmp_path), "NOPE", "1m") == 0


def test_instrument_label_is_compact():
    from vike_trader_app.data.instruments import InstrumentSpec
    from vike_trader_app.ui.datamanager_data import instrument_label

    assert instrument_label(InstrumentSpec("BTCUSDT", "crypto", 0.01)) == "crypto · tick 0.01"
    assert instrument_label(InstrumentSpec("EURUSD", "forex", 0.00001)) == "forex · tick 1e-05"


def test_instrument_detail_has_spec_and_profile():
    from vike_trader_app.data.instruments import InstrumentSpec
    from vike_trader_app.ui.datamanager_data import instrument_detail

    s = instrument_detail(InstrumentSpec("BTCUSDT", "crypto", 0.01, volume_step=1e-5), "Binance")
    assert "tick 0.01" in s and "2dp" in s and "Binance" in s


def test_quality_summary_empty():
    assert quality_summary([], _MIN) == "no data"


def test_quality_summary_clean_series():
    s = quality_summary([_b(i * _MIN) for i in range(5)], _MIN)
    assert "clean" in s and "5" in s


def test_quality_summary_flags_interior_gap():
    # 0,1, [missing 2,3], 4,5
    bars = [_b(0), _b(_MIN), _b(4 * _MIN), _b(5 * _MIN)]
    assert "gap" in quality_summary(bars, _MIN).lower()


def test_quality_summary_flags_ohlc_anomaly():
    bars = [_b(0), _b(_MIN, h=50.0, low=99.0)]  # high < low -> anomaly
    assert "high" in quality_summary(bars, _MIN).lower()


def test_delete_series_removes_all_files(tmp_path):
    from vike_trader_app.ui.datamanager_data import series_size_bytes
    from vike_trader_app.data.parquet_source import delete_series

    root = str(tmp_path)
    ps.append_series([_bar(1_700_000_000_000), _bar(1_701_500_000_000)], root, "X", "1m")
    assert series_size_bytes(root, "X", "1m") > 0
    delete_series(root, "X", "1m")
    assert series_size_bytes(root, "X", "1m") == 0
    assert not (tmp_path / "X" / "1m").exists()
