"""resolve_order + fetch_for: linked provider first, then the enabled chain in order.

Also covers Part 3: settings threading — fetch_for loads per-provider settings from
providers.json and forwards them to fetch_chain / select.
"""

from vike_trader_app.data import provider_chain as pcfg
from vike_trader_app.data.providers_config import ProviderEntry, ProvidersConfig, save_providers_config


def test_resolve_order_unlinked_uses_enabled_order():
    cfg = ProvidersConfig([ProviderEntry("binance", True), ProviderEntry("bybit", False),
                           ProviderEntry("okx", True)])
    assert pcfg.resolve_order("BTCUSDT", None, cfg) == ["binance", "okx"]


def test_resolve_order_linked_provider_goes_first_without_duplication():
    cfg = ProvidersConfig([ProviderEntry("binance", True), ProviderEntry("okx", True)])
    assert pcfg.resolve_order("BTCUSDT", "okx", cfg) == ["okx", "binance"]
    assert pcfg.resolve_order("BTCUSDT", "kraken", cfg) == ["kraken", "binance", "okx"]


def test_resolve_order_disabled_linked_is_still_promoted():
    # a DataSet linked to a provider overrides its disabled state in the config (promoted first)
    cfg = ProvidersConfig([ProviderEntry("binance", True), ProviderEntry("okx", False)])
    assert pcfg.resolve_order("BTCUSDT", "okx", cfg) == ["okx", "binance"]


def test_fetch_for_walks_config_chain(tmp_path):
    save_providers_config(ProvidersConfig([ProviderEntry("dead", True), ProviderEntry("good", True)]),
                          str(tmp_path))

    class _Src:
        def __init__(self, bars):
            self._bars = bars

        def fetch_bars_range(self, *a, **k):
            return self._bars

    def fake_select(symbol, provider=None, settings=None):
        return _Src([] if provider == "dead" else ["BAR"])

    bars, used = pcfg.fetch_for("BTCUSDT", "1m", 0, 10, root=str(tmp_path), select=fake_select)
    assert bars == ["BAR"] and used == "good"


# --- Part 3: settings threading ---

def test_fetch_for_passes_settings_to_select(tmp_path):
    """fetch_for loads persisted settings and forwards them to the select callable."""
    cfg = ProvidersConfig([
        ProviderEntry("binance", True, settings={"pause": 0.7, "base_url": "https://proxy.test"}),
        ProviderEntry("okx", True, settings={}),
    ])
    save_providers_config(cfg, str(tmp_path))

    captured = {}

    class _Src:
        def __init__(self, name):
            self.name = name

        def fetch_bars_range(self, *a, **k):
            return ["BAR"]

    def capturing_select(symbol, provider=None, settings=None):
        captured[provider] = settings
        return _Src(provider)

    pcfg.fetch_for("BTCUSDT", "1m", 0, 10, root=str(tmp_path), select=capturing_select)
    # binance should have been tried first and received the persisted settings
    assert captured.get("binance") == {"pause": 0.7, "base_url": "https://proxy.test"}


def test_fetch_chain_passes_settings_per_provider():
    """fetch_chain forwards settings_by_provider[name] to select for each provider."""
    received = {}

    class _Src:
        def __init__(self, name):
            self.name = name

        def fetch_bars_range(self, *a, **k):
            return ["BAR"]

    def sel(symbol, provider=None, settings=None):
        received[provider] = settings
        return _Src(provider)

    settings_map = {"binance": {"pause": 0.3}, "okx": {"base_url": "https://x.test"}}
    pcfg.fetch_chain(["binance", "okx"], "BTCUSDT", "1m", 0, 9,
                     select=sel, settings_by_provider=settings_map)
    assert received["binance"] == {"pause": 0.3}
    # okx not called because binance returned data first — only binance is in received
    assert "binance" in received


def test_fetch_chain_with_no_settings_by_provider_passes_none():
    """When settings_by_provider is omitted, select receives settings=None (backward-compat)."""
    received = {}

    class _Src:
        def fetch_bars_range(self, *a, **k):
            return []

    def sel(symbol, provider=None, settings=None):
        received[provider] = settings
        return _Src()

    pcfg.fetch_chain(["binance"], "BTCUSDT", "1m", 0, 9, select=sel)
    assert received["binance"] is None
