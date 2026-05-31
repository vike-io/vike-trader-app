"""Offscreen tests for the Journal tab (file-backed)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.analysis.journal import Journal  # noqa: E402
from vike_trader_app.ui.journal import JournalTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_add_persists_to_disk(app, tmp_path):
    path = str(tmp_path / "j.json")
    tab = JournalTab(journal=Journal(path))
    tab._title.setText("Clean breakout")
    tab._symbol.setText("EURUSD")
    tab._notes.setPlainText("watching 1.10")
    tab._add()
    assert tab._table.rowCount() == 1
    assert Journal(path).entries()[0].title == "Clean breakout"   # persisted


def test_empty_title_is_ignored(app, tmp_path):
    tab = JournalTab(journal=Journal(str(tmp_path / "j.json")))
    tab._add()                          # no title -> no-op
    assert tab._table.rowCount() == 0


def test_remove_reduces_count(app, tmp_path):
    tab = JournalTab(journal=Journal(str(tmp_path / "j.json")))
    tab._title.setText("A")
    tab._add()
    tab._title.setText("B")
    tab._add()
    assert tab._table.rowCount() == 2
    tab._table.setCurrentCell(0, 0)
    tab._remove()
    assert tab._table.rowCount() == 1
