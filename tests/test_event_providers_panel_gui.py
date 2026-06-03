"""Offscreen GUI tests for EventProvidersPanel (Part 3 of W3-C)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.event_providers_config import (  # noqa: E402
    default_event_providers,
    event_providers_path,
    load_event_providers_config,
)
from vike_trader_app.ui.event_providers_panel import EventProvidersPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_panel_lists_all_registry_providers(app, tmp_path):
    """Panel is populated with all providers from the default registry."""
    panel = EventProvidersPanel(root=str(tmp_path), parent=None)
    expected = default_event_providers()
    panel_names = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert panel_names == expected


def test_panel_all_checked_by_default(app, tmp_path):
    """All entries start as checked (enabled) when no config file exists."""
    panel = EventProvidersPanel(root=str(tmp_path), parent=None)
    from PySide6.QtCore import Qt
    for i in range(panel._list.count()):
        assert panel._list.item(i).checkState() == Qt.Checked


def test_panel_unchecking_persists_to_file(app, tmp_path):
    """Unchecking a provider immediately persists to event_providers.json."""
    root = str(tmp_path)
    panel = EventProvidersPanel(root=root, parent=None)

    # Find the FRED provider (a calendar actuals provider)
    target = "FRED"
    panel.set_enabled(target, False)

    # File should now exist and contain FRED as disabled
    path = event_providers_path(root)
    assert path.exists(), "event_providers.json was not created"

    cfg = load_event_providers_config(root)
    entry = next((p for p in cfg.providers if p.name == target), None)
    assert entry is not None
    assert entry.enabled is False


def test_panel_enabled_names_excludes_unchecked(app, tmp_path):
    """enabled_names() returns only the checked provider names."""
    panel = EventProvidersPanel(root=str(tmp_path), parent=None)
    panel.set_enabled("FRED", False)
    panel.set_enabled("BLS", False)

    names = panel.enabled_names()
    assert "FRED" not in names
    assert "BLS" not in names
    # All others should still be present
    for name in default_event_providers():
        if name not in ("FRED", "BLS"):
            assert name in names


def test_panel_current_config_reflects_state(app, tmp_path):
    """current_config() returns an EventProvidersConfig matching the list widget state."""
    panel = EventProvidersPanel(root=str(tmp_path), parent=None)
    panel.set_enabled("ECB", False)

    cfg = panel.current_config()
    entry = next((p for p in cfg.providers if p.name == "ECB"), None)
    assert entry is not None and entry.enabled is False

    # Re-enable
    panel.set_enabled("ECB", True)
    cfg2 = panel.current_config()
    entry2 = next((p for p in cfg2.providers if p.name == "ECB"), None)
    assert entry2 is not None and entry2.enabled is True


def test_panel_reloads_existing_config(app, tmp_path):
    """A second panel instance reads the persisted config from the first."""
    root = str(tmp_path)
    p1 = EventProvidersPanel(root=root, parent=None)
    p1.set_enabled("CoinDesk", False)

    # Second instance should load the persisted state
    p2 = EventProvidersPanel(root=root, parent=None)
    cfg = p2.current_config()
    entry = next((p for p in cfg.providers if p.name == "CoinDesk"), None)
    assert entry is not None and entry.enabled is False
