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
