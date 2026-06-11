"""Unit tests for streaming_providers_config — pure (no Qt)."""

import json

from vike_trader_app.data.providers_config import DEFAULT_ORDER
from vike_trader_app.data.streaming_providers_config import (
    StreamingProviderEntry,
    StreamingProvidersConfig,
    default_streaming_providers,
    load_streaming_providers_config,
    save_streaming_providers_config,
    streaming_kind,
    streaming_providers_path,
)


def test_default_list_matches_default_order():
    assert default_streaming_providers() == DEFAULT_ORDER


def test_config_default_builds_all_enabled():
    cfg = StreamingProvidersConfig.default()
    assert [p.name for p in cfg.providers] == DEFAULT_ORDER
    assert all(p.enabled for p in cfg.providers)


def test_enabled_in_order_filters_disabled():
    cfg = StreamingProvidersConfig([
        StreamingProviderEntry("binance", True),
        StreamingProviderEntry("bybit", False),
        StreamingProviderEntry("okx", True),
    ])
    assert cfg.enabled_in_order() == ["binance", "okx"]


def test_streaming_kind_binance_is_push():
    assert streaming_kind("binance") == "push"


def test_streaming_kind_yahoo_is_poll():
    assert streaming_kind("yahoo") == "poll"


def test_streaming_kind_bybit_is_poll():
    assert streaming_kind("bybit") == "poll"


def test_streaming_kind_okx_is_poll():
    assert streaming_kind("okx") == "poll"


def test_streaming_kind_coinbase_is_poll():
    assert streaming_kind("coinbase") == "poll"


def test_streaming_kind_kraken_is_poll():
    assert streaming_kind("kraken") == "poll"


def test_streaming_kind_dukascopy_is_poll():
    assert streaming_kind("dukascopy") == "poll"


def test_streaming_kind_unknown_is_poll():
    assert streaming_kind("nonexistent_provider") == "poll"


def test_save_load_round_trip(tmp_path):
    cfg = StreamingProvidersConfig([
        StreamingProviderEntry("binance", True),
        StreamingProviderEntry("bybit", False),
        StreamingProviderEntry("okx", True),
    ])
    save_streaming_providers_config(cfg, str(tmp_path))
    loaded = load_streaming_providers_config(str(tmp_path))
    assert [(p.name, p.enabled) for p in loaded.providers[:3]] == [
        ("binance", True),
        ("bybit", False),
        ("okx", True),
    ]


def test_save_persists_to_app_db_not_json(tmp_path):
    """State-in-DB rule: save writes the app DB under <root>/db, never a loose JSON file."""
    cfg = StreamingProvidersConfig.default()
    save_streaming_providers_config(cfg, str(tmp_path))
    assert not streaming_providers_path(str(tmp_path)).exists()
    assert (tmp_path / "db" / "vike_trader_app.sqlite").exists()
    assert load_streaming_providers_config(str(tmp_path)).providers[0].name == DEFAULT_ORDER[0]


def test_legacy_json_migrates_into_db_then_file_deleted(tmp_path):
    """One-time sweep: a legacy streaming_providers.json is imported, then removed."""
    legacy = streaming_providers_path(str(tmp_path))
    legacy.write_text(json.dumps([{"name": "kraken", "enabled": False}]), encoding="utf-8")
    loaded = load_streaming_providers_config(str(tmp_path))
    by_name = {p.name: p.enabled for p in loaded.providers}
    assert by_name["kraken"] is False            # the saved flag survived the migration
    assert not legacy.exists()                   # and the legacy file is gone


def test_load_no_file_returns_default(tmp_path):
    cfg = load_streaming_providers_config(str(tmp_path))
    assert [p.name for p in cfg.providers] == DEFAULT_ORDER
    assert all(p.enabled for p in cfg.providers)


def test_load_back_compat_appends_new_providers(tmp_path):
    """A saved file missing a provider gets the new provider appended (disabled)."""
    # Write a partial list (only first two providers)
    partial = [{"name": "binance", "enabled": True}, {"name": "bybit", "enabled": True}]
    streaming_providers_path(str(tmp_path)).write_text(
        json.dumps(partial), encoding="utf-8"
    )
    loaded = load_streaming_providers_config(str(tmp_path))
    names = [p.name for p in loaded.providers]
    # All DEFAULT_ORDER names must be present
    for name in DEFAULT_ORDER:
        assert name in names
    # The new (appended) providers should be disabled
    appended = {p.name: p.enabled for p in loaded.providers if p.name not in {"binance", "bybit"}}
    assert all(not enabled for enabled in appended.values())
