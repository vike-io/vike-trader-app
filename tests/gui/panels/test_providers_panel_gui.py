# tests/test_providers_panel_gui.py
"""Offscreen tests for the Historical Providers panel (Parts 1-4)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.providers_config import DEFAULT_ORDER, load_providers_config  # noqa: E402
from vike_trader_app.ui.providers_panel import ProvidersPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_lists_all_providers_checked_by_default(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    assert panel.current_order() == DEFAULT_ORDER
    assert panel.enabled_names() == DEFAULT_ORDER


def test_uncheck_persists_to_config(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    panel.set_enabled("binance", False)        # dialog-free
    assert "binance" not in panel.enabled_names()
    assert "binance" not in load_providers_config(str(tmp_path)).enabled_in_order()


def test_testbed_reports_via_chain(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    captured = []
    panel.testbed_result.connect(captured.append)
    panel.run_testbed("BTCUSDT", "1m", fetch=lambda *a, **k: (["BAR", "BAR"], "binance"))
    assert captured and "binance" in captured[0] and "2" in captured[0]


# --- Part 4: settings form ---

def test_set_provider_setting_persists_to_json(app, tmp_path):
    """set_provider_setting writes to providers.json and can be reloaded."""
    panel = ProvidersPanel(str(tmp_path))
    panel.set_provider_setting("binance", "pause", 0.5)
    # Verify in-memory
    assert panel.provider_settings("binance").get("pause") == 0.5
    # Verify persisted to JSON
    cfg = load_providers_config(str(tmp_path))
    binance = next(p for p in cfg.providers if p.name == "binance")
    assert binance.settings.get("pause") == 0.5


def test_set_provider_setting_base_url_persists(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    panel.set_provider_setting("okx", "base_url", "https://proxy.test")
    assert panel.provider_settings("okx").get("base_url") == "https://proxy.test"
    cfg = load_providers_config(str(tmp_path))
    okx = next(p for p in cfg.providers if p.name == "okx")
    assert okx.settings.get("base_url") == "https://proxy.test"


def test_settings_survive_reload(app, tmp_path):
    """Settings persisted by one panel instance are available on reload."""
    panel1 = ProvidersPanel(str(tmp_path))
    panel1.set_provider_setting("kraken", "pause", 1.2)
    # Create a new panel instance (simulates app restart) — settings come from JSON
    panel2 = ProvidersPanel(str(tmp_path))
    assert panel2.provider_settings("kraken").get("pause") == 1.2


def test_current_config_includes_settings(app, tmp_path):
    """current_config() returns ProviderEntry objects with the current settings dict."""
    panel = ProvidersPanel(str(tmp_path))
    panel.set_provider_setting("bybit", "pause", 0.3)
    cfg = panel.current_config()
    bybit = next(p for p in cfg.providers if p.name == "bybit")
    assert bybit.settings.get("pause") == 0.3


def test_yahoo_and_dukascopy_have_no_settings_form(app, tmp_path):
    """Providers with no fields show no settings group box."""
    from vike_trader_app.data.provider_settings import fields_for
    assert fields_for("yahoo") == []
    assert fields_for("dukascopy") == []
    # Panel should still construct without error for these providers
    panel = ProvidersPanel(str(tmp_path))
    # provider_settings returns empty dict for these
    assert panel.provider_settings("yahoo") == {}
    assert panel.provider_settings("dukascopy") == {}


def test_set_provider_setting_api_key_env_persists(app, tmp_path):
    panel = ProvidersPanel(str(tmp_path))
    panel.set_provider_setting("binance", "api_key_env", "MY_BINANCE_KEY")
    cfg = load_providers_config(str(tmp_path))
    binance = next(p for p in cfg.providers if p.name == "binance")
    assert binance.settings.get("api_key_env") == "MY_BINANCE_KEY"
