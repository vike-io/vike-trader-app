"""Persisted Historical-Providers chain config (enable + priority order)."""

from vike_trader_app.data import providers_config as pc


def test_default_order_and_all_enabled():
    cfg = pc.ProvidersConfig.default()
    assert [p.name for p in cfg.providers] == pc.DEFAULT_ORDER
    assert all(p.enabled for p in cfg.providers)
    assert cfg.enabled_in_order() == pc.DEFAULT_ORDER


def test_save_load_roundtrip_preserves_order_and_flags(tmp_path):
    cfg = pc.ProvidersConfig.default()
    cfg.providers[0].enabled = False          # disable binance
    cfg.providers.append(cfg.providers.pop(1))  # move bybit to the end
    pc.save_providers_config(cfg, str(tmp_path))
    back = pc.load_providers_config(str(tmp_path))
    assert [(p.name, p.enabled) for p in back.providers] == [(p.name, p.enabled) for p in cfg.providers]
    assert "binance" not in back.enabled_in_order()


def test_load_missing_file_returns_default(tmp_path):
    assert pc.load_providers_config(str(tmp_path)).enabled_in_order() == pc.DEFAULT_ORDER


def test_load_merges_newly_added_providers(tmp_path):
    cfg = pc.ProvidersConfig(providers=[pc.ProviderEntry("binance", True)])
    pc.save_providers_config(cfg, str(tmp_path))
    back = pc.load_providers_config(str(tmp_path))
    names = [p.name for p in back.providers]
    assert names[0] == "binance"
    assert set(names) == set(pc.DEFAULT_ORDER)
    assert back.providers[names.index("yahoo")].enabled is False


# --- Part 2: settings field ---

def test_provider_entry_default_settings_is_empty_dict():
    entry = pc.ProviderEntry("binance")
    assert entry.settings == {}


def test_settings_roundtrip_with_pause(tmp_path):
    cfg = pc.ProvidersConfig.default()
    # Set a custom setting on binance
    for p in cfg.providers:
        if p.name == "binance":
            p.settings = {"pause": 0.5}
    pc.save_providers_config(cfg, str(tmp_path))
    back = pc.load_providers_config(str(tmp_path))
    binance = next(p for p in back.providers if p.name == "binance")
    assert binance.settings == {"pause": 0.5}


def test_old_format_entry_loads_with_empty_settings(tmp_path):
    """Back-compat: an old providers.json without 'settings' key loads with settings == {}."""
    import json
    # Write old-format JSON (no 'settings' key on any entry)
    old_data = [{"name": "binance", "enabled": True}, {"name": "bybit", "enabled": False}]
    (tmp_path / "providers.json").write_text(json.dumps(old_data), encoding="utf-8")
    back = pc.load_providers_config(str(tmp_path))
    for p in back.providers:
        if p.name in ("binance", "bybit"):
            assert p.settings == {}, f"{p.name} should have empty settings"


def test_settings_with_base_url_roundtrip(tmp_path):
    cfg = pc.ProvidersConfig(providers=[
        pc.ProviderEntry("okx", True, settings={"base_url": "https://proxy.example.com", "pause": 0.2}),
    ])
    pc.save_providers_config(cfg, str(tmp_path))
    back = pc.load_providers_config(str(tmp_path))
    okx = next(p for p in back.providers if p.name == "okx")
    assert okx.settings["base_url"] == "https://proxy.example.com"
    assert okx.settings["pause"] == 0.2
