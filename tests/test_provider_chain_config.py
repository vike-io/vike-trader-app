"""resolve_order + fetch_for: linked provider first, then the enabled chain in order."""

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

    def fake_select(symbol, provider=None):
        return _Src([] if provider == "dead" else ["BAR"])

    bars, used = pcfg.fetch_for("BTCUSDT", "1m", 0, 10, root=str(tmp_path), select=fake_select)
    assert bars == ["BAR"] and used == "good"
