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
