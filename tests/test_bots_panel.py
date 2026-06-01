"""Offscreen tests for the Bots panel."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.bots_panel import BotsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_bots_panel_tabs_and_button(app):
    panel = BotsPanel()
    titles = [panel._tabs.tabText(i) for i in range(panel._tabs.count())]
    assert titles == ["Active Bots", "Historic Runs"]
    assert panel.launch_btn.text() == "🚀 Launch Bot"


def test_launch_button_emits_signal(app):
    panel = BotsPanel()
    fired = []
    panel.launchRequested.connect(lambda: fired.append(True))
    panel.launch_btn.click()
    assert fired == [True]


def test_exposes_strategy_and_history(app):
    panel = BotsPanel()
    assert panel.strategy is not None
    assert panel.history is not None
