"""DataSets — named symbol collections (Wealth-Lab concept) + JSON storage."""

from vike_trader_app.data import datasets as ds
from vike_trader_app.data.datasets import DataSet


def test_parse_symbols_splits_dedupes_upper():
    assert ds.parse_symbols("btcusdt, ethusdt\n solusdt , btcusdt") == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert ds.parse_symbols("") == []


def test_save_load_roundtrip(tmp_path):
    d = DataSet(name="My Crypto", symbols=["BTCUSDT", "ETHUSDT"], provider="bybit", interval="5m")
    ds.save_dataset(d, str(tmp_path))
    assert ds.load_dataset("My Crypto", str(tmp_path)) == d


def test_load_missing_returns_none(tmp_path):
    assert ds.load_dataset("nope", str(tmp_path)) is None


def test_list_and_delete(tmp_path):
    ds.save_dataset(DataSet("A", ["X"]), str(tmp_path))
    ds.save_dataset(DataSet("B", ["Y"]), str(tmp_path))
    assert ds.list_datasets(str(tmp_path)) == ["A", "B"]
    ds.delete_dataset("A", str(tmp_path))
    assert ds.list_datasets(str(tmp_path)) == ["B"]


def test_provider_defaults_to_none_auto(tmp_path):
    ds.save_dataset(DataSet("D", ["BTCUSDT"]), str(tmp_path))
    back = ds.load_dataset("D", str(tmp_path))
    assert back.provider is None and back.interval == "1m"


def test_ensure_examples_seeds_once(tmp_path):
    written = ds.ensure_examples(str(tmp_path))
    assert "Crypto Majors" in written
    assert "Crypto Majors" in ds.list_datasets(str(tmp_path))
    assert ds.ensure_examples(str(tmp_path)) == []  # idempotent


def test_provider_group_buckets_by_linked_provider_then_symbol():
    # explicit linked provider wins
    assert ds.provider_group(DataSet("x", ["BTCUSDT"], provider="binance")) == "Binance"
    assert ds.provider_group(DataSet("x", ["EURUSD"], provider="dukascopy")) == "Dukascopy"
    # unlinked -> inferred from the symbols (FX symbol vs crypto symbol)
    assert ds.provider_group(DataSet("x", ["EURUSD"])) == "Dukascopy"
    assert ds.provider_group(DataSet("x", ["BTCUSDT"])) == "Binance"
    # unlinked + empty -> ungrouped (My DataSets only)
    assert ds.provider_group(DataSet("x", [])) is None


def test_fx_preset_seeded_and_dukascopy_linked():
    presets = ds.preset_datasets()
    assert "FX Majors" in presets
    assert presets["FX Majors"].provider == "dukascopy"
    assert "EURUSD" in presets["FX Majors"].symbols


def test_daterange_and_is_dynamic():
    from vike_trader_app.data.datasets import DataSet, DateRange
    plain = DataSet("P", ["BTCUSDT"])
    assert plain.ranges == {} and plain.is_dynamic() is False
    dyn = DataSet("D", ["AAA", "BBB"], ranges={"AAA": [DateRange(1000, 2000)]})
    assert dyn.is_dynamic() is True


def test_active_at_respects_windows():
    from vike_trader_app.data.datasets import DataSet, DateRange
    # AAA member 1000..2000 (inclusive); BBB has an open-ended window from 3000
    d = DataSet("D", ["AAA", "BBB"], ranges={"AAA": [DateRange(1000, 2000)], "BBB": [DateRange(3000, None)]})
    assert d.active_at("AAA", 999) is False
    assert d.active_at("AAA", 1000) is True
    assert d.active_at("AAA", 2000) is True
    assert d.active_at("AAA", 2001) is False
    assert d.active_at("BBB", 5000) is True       # open-ended
    assert d.active_at("CCC", 5000) is True        # no ranges for a symbol -> always active
    assert d.active_at("AAA", 1500) is True


def test_save_load_roundtrips_ranges(tmp_path):
    from vike_trader_app.data import datasets as ds
    from vike_trader_app.data.datasets import DataSet, DateRange
    d = DataSet("D", ["AAA", "BBB"], provider="binance", interval="1d",
                ranges={"AAA": [DateRange(1000, 2000), DateRange(4000, None)]})
    ds.save_dataset(d, str(tmp_path))
    back = ds.load_dataset("D", str(tmp_path))
    assert back == d
    assert back.ranges["AAA"] == [DateRange(1000, 2000), DateRange(4000, None)]
    assert back.is_dynamic() is True


def test_load_old_format_without_ranges_is_back_compat(tmp_path):
    import json
    from pathlib import Path
    from vike_trader_app.data import datasets as ds
    # an OLD-format file written before ranges existed (no "ranges" key)
    p = Path(str(tmp_path)) / "datasets"
    p.mkdir(parents=True, exist_ok=True)
    (p / "old.json").write_text(json.dumps({"name": "old", "symbols": ["X"], "provider": None, "interval": "1m"}))
    back = ds.load_dataset("old", str(tmp_path))
    assert back is not None and back.symbols == ["X"] and back.ranges == {} and back.is_dynamic() is False


# --- benchmark field ---

def test_benchmark_field_defaults_to_empty_string():
    d = DataSet("T", ["BTCUSDT"])
    assert d.benchmark == ""


def test_benchmark_roundtrips_through_to_dict_from_dict():
    from vike_trader_app.data.datasets import _dataset_to_dict, _dataset_from_dict
    d = DataSet("T", ["BTCUSDT", "ETHUSDT"], provider="binance", interval="1h", benchmark="BTCUSDT")
    back = _dataset_from_dict(_dataset_to_dict(d))
    assert back.benchmark == "BTCUSDT"
    assert back == d


def test_benchmark_roundtrips_through_save_load(tmp_path):
    d = DataSet("BenchDS", ["AAPL", "MSFT"], provider=None, interval="1d", benchmark="SPY")
    ds.save_dataset(d, str(tmp_path))
    back = ds.load_dataset("BenchDS", str(tmp_path))
    assert back is not None
    assert back.benchmark == "SPY"
    assert back == d


def test_benchmark_absent_key_loads_as_empty_string(tmp_path):
    """Old JSON files without a 'benchmark' key must load with benchmark='' (back-compat)."""
    import json
    from pathlib import Path
    p = Path(str(tmp_path)) / "datasets"
    p.mkdir(parents=True, exist_ok=True)
    # Write a file that has no "benchmark" key
    (p / "old-no-bench.json").write_text(
        json.dumps({"name": "old-no-bench", "symbols": ["X"], "provider": None, "interval": "1m"})
    )
    back = ds.load_dataset("old-no-bench", str(tmp_path))
    assert back is not None and back.benchmark == ""


def test_benchmark_empty_string_roundtrips(tmp_path):
    """An explicitly empty benchmark saves and loads as ''."""
    d = DataSet("NoBench", ["X"], benchmark="")
    ds.save_dataset(d, str(tmp_path))
    back = ds.load_dataset("NoBench", str(tmp_path))
    assert back is not None and back.benchmark == ""
