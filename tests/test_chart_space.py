"""Offscreen tests for the Chart space layout + Bots wiring on the main window."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.bots_panel import BotsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_main_window_uses_bots_panel(app):
    win = MainWindow()
    assert isinstance(win.bots, BotsPanel)
    # equity chart is not parented into a visible layout (Studio owns equity now)
    assert win.equity.parent() is None


def test_first_space_is_chart(app):
    win = MainWindow()
    assert win._RAIL_ITEMS[0][1] == "Chart"
    assert win._mode_tag.text() == "CHART"
