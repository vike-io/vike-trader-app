"""Offscreen tests for the Tools tab (calculator cards)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.tools import ToolsTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_tools_tab_builds_cards(app):
    tab = ToolsTab()
    assert len(tab.cards) == 6


def test_position_size_card_computes_default(app):
    tab = ToolsTab()
    card = tab.cards[0]  # Position size: account 10k, risk 1%, entry 100, stop 95 -> qty 20
    assert "20" in card._out.text()


def test_card_recomputes_on_input_change(app):
    tab = ToolsTab()
    card = tab.cards[0]
    before = card._out.text()
    card._fields["risk_pct"][1].setValue(2.0)  # double the risk -> qty doubles
    assert card._out.text() != before
    assert "40" in card._out.text()
