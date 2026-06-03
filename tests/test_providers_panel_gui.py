# tests/test_providers_panel_gui.py
"""Offscreen tests for the Historical Providers panel."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

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
