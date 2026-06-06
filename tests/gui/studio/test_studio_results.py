"""Offscreen tests for Studio's ResultsPanel tab structure."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.studio import ResultsPanel  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _tab_titles(panel):
    return [panel._tabs.tabText(i) for i in range(panel._tabs.count())]


def test_results_panel_has_equity_tab_not_chart(app):
    panel = ResultsPanel()
    titles = _tab_titles(panel)
    assert "Equity" in titles
    assert "Chart" not in titles
    assert hasattr(panel, "_equity")
    assert not hasattr(panel, "_price")


def test_results_panel_tab_order(app):
    panel = ResultsPanel()
    assert _tab_titles(panel) == [
        "Equity", "Performance", "Trades", "By Symbol", "Runs", "Distribution",
        "Robustness", "Monte Carlo", "Periods", "Benchmark", "WF Matrix", "Surface",
    ]


def test_mount_chart_tab_appends_chart(app):
    panel = ResultsPanel()
    panel.mount_chart_tab(QtWidgets.QWidget())
    assert _tab_titles(panel) == [
        "Equity", "Performance", "Trades", "By Symbol", "Runs", "Distribution",
        "Robustness", "Monte Carlo", "Periods", "Benchmark", "WF Matrix", "Surface", "Chart",
    ]
