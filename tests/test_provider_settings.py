"""Tests for the per-provider settings schema (provider_settings.py)."""

import pytest

from vike_trader_app.data.provider_settings import FieldSpec, defaults_for, fields_for


# --- fields_for ---

def test_fields_for_binance_nonempty_with_pause_and_base_url():
    fields = fields_for("binance")
    assert len(fields) > 0
    names = [f.name for f in fields]
    assert "pause" in names
    assert "base_url" in names


def test_fields_for_binance_pause_is_float():
    fields = {f.name: f for f in fields_for("binance")}
    assert fields["pause"].kind == "float"
    assert fields["pause"].default == 0.0


def test_fields_for_binance_base_url_is_str():
    fields = {f.name: f for f in fields_for("binance")}
    assert fields["base_url"].kind == "str"
    assert fields["base_url"].default == ""


def test_fields_for_binance_api_key_env_present():
    names = [f.name for f in fields_for("binance")]
    assert "api_key_env" in names


def test_fields_for_all_crypto_providers_have_same_fields():
    """All five crypto REST sources accept base_url and pause."""
    providers = ["binance", "bybit", "okx", "coinbase", "kraken"]
    ref_names = [f.name for f in fields_for("binance")]
    for p in providers:
        assert [f.name for f in fields_for(p)] == ref_names, f"{p} fields differ from binance"


def test_fields_for_yahoo_returns_empty_list():
    assert fields_for("yahoo") == []


def test_fields_for_dukascopy_returns_empty_list():
    assert fields_for("dukascopy") == []


def test_fields_for_unknown_provider_returns_empty_list():
    assert fields_for("nonexistent_provider_xyz") == []


# --- defaults_for ---

def test_defaults_for_binance_returns_correct_keys_and_values():
    d = defaults_for("binance")
    assert "pause" in d and d["pause"] == 0.0
    assert "base_url" in d and d["base_url"] == ""
    assert "api_key_env" in d and d["api_key_env"] == ""


def test_defaults_for_yahoo_returns_empty_dict():
    assert defaults_for("yahoo") == {}


def test_defaults_for_dukascopy_returns_empty_dict():
    assert defaults_for("dukascopy") == {}


def test_defaults_for_unknown_provider_returns_empty_dict():
    assert defaults_for("nope") == {}


def test_fieldspec_dataclass_fields():
    """FieldSpec is a plain dataclass with the documented attributes."""
    f = FieldSpec(name="pause", kind="float", default=0.5, hint="test hint")
    assert f.name == "pause"
    assert f.kind == "float"
    assert f.default == 0.5
    assert f.hint == "test hint"
    assert f.choices is None
