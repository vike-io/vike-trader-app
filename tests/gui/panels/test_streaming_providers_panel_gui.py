"""Offscreen GUI tests for StreamingProvidersPanel."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.data.providers_config import DEFAULT_ORDER  # noqa: E402
from vike_trader_app.data.streaming_providers_config import (  # noqa: E402
    load_streaming_providers_config,
    streaming_kind,
)
from vike_trader_app.ui.streaming_providers_panel import StreamingProvidersPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_panel_lists_default_order(app, tmp_path):
    """All DEFAULT_ORDER providers appear in the panel, in order."""
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    assert panel._list.count() == len(DEFAULT_ORDER)
    for i, name in enumerate(DEFAULT_ORDER):
        item = panel._list.item(i)
        stored_name = item.data(QtCore.Qt.UserRole)
        assert stored_name == name


def test_panel_labels_include_push_or_poll(app, tmp_path):
    """Each row label contains '· push' or '· poll'."""
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        name = item.data(QtCore.Qt.UserRole)
        kind = streaming_kind(name)
        assert kind in item.text(), f"Row {name!r} label {item.text()!r} missing kind {kind!r}"


def test_binance_row_labelled_push(app, tmp_path):
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        if item.data(QtCore.Qt.UserRole) == "binance":
            assert "push" in item.text()
            return
    pytest.fail("binance row not found")


def test_yahoo_row_labelled_poll(app, tmp_path):
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        if item.data(QtCore.Qt.UserRole) == "yahoo":
            assert "poll" in item.text()
            return
    pytest.fail("yahoo row not found")


def test_all_providers_checked_by_default(app, tmp_path):
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    for i in range(panel._list.count()):
        item = panel._list.item(i)
        assert item.checkState() == QtCore.Qt.Checked, (
            f"Expected {item.data(QtCore.Qt.UserRole)!r} to be checked by default"
        )


def test_uncheck_persists_to_json(app, tmp_path):
    """Unchecking a provider writes the change to streaming_providers.json."""
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    # Uncheck "bybit" via set_enabled
    panel.set_enabled("bybit", False)
    # Reload from disk
    cfg = load_streaming_providers_config(str(tmp_path))
    by_name = {p.name: p.enabled for p in cfg.providers}
    assert by_name["bybit"] is False
    # Others remain enabled
    assert by_name["binance"] is True


def test_set_enabled_toggles_check_state(app, tmp_path):
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    panel.set_enabled("okx", False)
    assert "okx" not in panel.enabled_names()
    panel.set_enabled("okx", True)
    assert "okx" in panel.enabled_names()


def test_current_config_reflects_state(app, tmp_path):
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    panel.set_enabled("kraken", False)
    cfg = panel.current_config()
    by_name = {p.name: p.enabled for p in cfg.providers}
    assert by_name["kraken"] is False
    assert by_name["binance"] is True


def test_enabled_names_excludes_unchecked(app, tmp_path):
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    panel.set_enabled("dukascopy", False)
    assert "dukascopy" not in panel.enabled_names()
    assert "binance" in panel.enabled_names()


def test_panel_has_explanatory_label(app, tmp_path):
    """The panel includes a QLabel with an explanatory note about routing."""
    panel = StreamingProvidersPanel(str(tmp_path), parent=None)
    labels = panel.findChildren(QtWidgets.QLabel)
    texts = [lbl.text() for lbl in labels]
    assert any("WebSocket" in t for t in texts), (
        f"No label mentioning WebSocket found; labels: {texts}"
    )
