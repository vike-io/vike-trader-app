"""Tests for the event-providers config store (Part 1 of W3-C)."""

import json

from vike_trader_app.data.event_providers_config import (
    EventProviderEntry,
    EventProvidersConfig,
    default_event_providers,
    enabled_event_providers,
    event_providers_path,
    load_event_providers_config,
    save_event_providers_config,
)


def test_default_includes_all_news_provider_names():
    from vike_trader_app.data.news.providers import PROVIDERS as NEWS_PROVIDERS
    news_names = {spec.name for spec in NEWS_PROVIDERS}
    defaults = set(default_event_providers())
    assert news_names <= defaults, f"Missing news providers: {news_names - defaults}"


def test_default_includes_calendar_provider_names():
    expected_calendar = {"ForexFactory", "FRED", "BLS", "BEA", "Census", "ECB"}
    defaults = set(default_event_providers())
    assert expected_calendar <= defaults, f"Missing calendar providers: {expected_calendar - defaults}"


def test_default_providers_no_duplicates():
    names = default_event_providers()
    assert len(names) == len(set(names)), "Duplicate names in default_event_providers()"


def test_config_default_all_enabled():
    cfg = EventProvidersConfig.default()
    assert all(e.enabled for e in cfg.providers)
    assert len(cfg.providers) == len(default_event_providers())


def test_enabled_in_order_respects_disabled():
    cfg = EventProvidersConfig([
        EventProviderEntry("A", True),
        EventProviderEntry("B", False),
        EventProviderEntry("C", True),
    ])
    assert cfg.enabled_in_order() == ["A", "C"]


def test_save_load_round_trip(tmp_path):
    root = str(tmp_path)
    cfg = EventProvidersConfig([
        EventProviderEntry("CoinDesk", True),
        EventProviderEntry("FRED", False),
        EventProviderEntry("ForexFactory", True),
    ])
    save_event_providers_config(cfg, root)

    loaded = load_event_providers_config(root)
    # The three explicit entries should come first in the same order
    first_three = loaded.providers[:3]
    assert first_three[0].name == "CoinDesk" and first_three[0].enabled is True
    assert first_three[1].name == "FRED" and first_three[1].enabled is False
    assert first_three[2].name == "ForexFactory" and first_three[2].enabled is True


def test_load_appends_new_providers_as_enabled_when_missing(tmp_path):
    """A config written by an older build (missing some providers) gets them appended as enabled."""
    root = str(tmp_path)
    # Write a minimal config that only has one provider
    path = event_providers_path(root)
    path.write_text(json.dumps([{"name": "FRED", "enabled": True}]), encoding="utf-8")

    loaded = load_event_providers_config(root)
    names = [p.name for p in loaded.providers]
    # FRED is first (from file); all registered defaults that were absent should appear
    assert names[0] == "FRED"
    # All default providers should be present
    for name in default_event_providers():
        assert name in names, f"{name} missing from back-compat load"


def test_load_returns_default_when_no_file(tmp_path):
    root = str(tmp_path)
    cfg = load_event_providers_config(root)
    # Should be identical to the default config
    assert [p.name for p in cfg.providers] == default_event_providers()
    assert all(p.enabled for p in cfg.providers)


def test_enabled_event_providers_returns_none_when_no_file(tmp_path):
    result = enabled_event_providers(str(tmp_path))
    assert result is None


def test_enabled_event_providers_returns_set_when_file_exists(tmp_path):
    root = str(tmp_path)
    cfg = EventProvidersConfig([
        EventProviderEntry("CoinDesk", True),
        EventProviderEntry("FRED", False),
        EventProviderEntry("ECB", True),
    ])
    save_event_providers_config(cfg, root)

    result = enabled_event_providers(root)
    assert result is not None
    assert isinstance(result, set)
    assert "CoinDesk" in result
    assert "ECB" in result
    assert "FRED" not in result


def test_enabled_event_providers_returns_set_matching_enabled_in_order(tmp_path):
    root = str(tmp_path)
    cfg = EventProvidersConfig.default()
    # Disable a couple of entries
    cfg.providers[0].enabled = False
    cfg.providers[1].enabled = False
    save_event_providers_config(cfg, root)

    result = enabled_event_providers(root)
    expected = set(cfg.enabled_in_order())
    assert result == expected


# --- state-in-DB migration ---

def test_legacy_event_providers_json_migrates_then_file_deleted(tmp_path):
    """One-time sweep: a legacy event_providers.json is imported into the app DB, then removed."""
    root = str(tmp_path)
    legacy = event_providers_path(root)
    legacy.write_text(json.dumps([{"name": "FRED", "enabled": False}]), encoding="utf-8")
    enabled = enabled_event_providers(root)
    assert enabled is not None and "FRED" not in enabled   # the saved flag survived
    assert not legacy.exists()                             # legacy file deleted
    assert (tmp_path / "db" / "vike_trader_app.sqlite").exists()   # ... into the app DB


def test_save_persists_to_app_db_not_json(tmp_path):
    """State-in-DB rule: save writes the app DB under <root>/db, never a loose JSON file."""
    root = str(tmp_path)
    save_event_providers_config(EventProvidersConfig.default(), root)
    assert not event_providers_path(root).exists()
    assert (tmp_path / "db" / "vike_trader_app.sqlite").exists()
