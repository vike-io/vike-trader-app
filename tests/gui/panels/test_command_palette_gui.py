"""Offscreen tests for the Ctrl+K command palette (Phase 5)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

import vike_trader_app.ui.chartdoc as chartdoc  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.command_palette import CommandPalette  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _synthetic_load(monkeypatch):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(30)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))


# --- the palette widget itself --------------------------------------------------------------


def test_palette_filters_and_runs(app):
    fired = []
    cmds = [("Go to Chart", lambda: fired.append("chart")),
            ("Go to Studio", lambda: fired.append("studio")),
            ("Save workspace as…", lambda: fired.append("save"))]
    pal = CommandPalette(cmds)
    assert pal.current_labels() == ["Go to Chart", "Go to Studio", "Save workspace as…"]

    pal.set_query("studio")
    assert pal.current_labels() == ["Go to Studio"]
    pal.activate(0)
    assert fired == ["studio"]


def test_palette_empty_query_shows_all(app):
    pal = CommandPalette([("A", lambda: None), ("B", lambda: None)])
    pal.set_query("zzz")
    assert pal.current_labels() == []
    pal.set_query("")
    assert pal.current_labels() == ["A", "B"]


# --- MainWindow command wiring --------------------------------------------------------------


def test_mainwindow_commands_cover_spaces_and_workspaces(app):
    win = MainWindow(session_path=None)
    labels = [label for label, _cb in win._commands()]
    assert "Go to Chart" in labels
    assert "Go to Studio" in labels
    assert any(l.startswith("Open workspace: Trading") for l in labels)
    assert any(l.startswith("New chart:") for l in labels)
    assert "Save workspace as…" in labels
    assert any(l.startswith("Toggle panel:") for l in labels)
    win.close()


def test_command_go_to_space_switches(app):
    win = MainWindow(session_path=None)
    cmds = dict(win._commands())
    cmds["Go to Studio"]()
    assert win.tabs.currentWidget() is win.studio
    win.close()


def test_command_open_workspace_applies(app):
    win = MainWindow(session_path=None)
    cmds = dict(win._commands())
    cmds["Open workspace: Research  (built-in)" if "Open workspace: Research  (built-in)" in cmds
         else "Open workspace: Research"]()
    assert [d.symbol for d in win._doc_widgets] == ["ETHUSDT", "SOLUSDT"]
    win.close()


def test_command_new_chart_opens_document(app):
    win = MainWindow(session_path=None)
    cmds = dict(win._commands())
    new_cmd = next(label for label in cmds if label.startswith("New chart:"))
    cmds[new_cmd]()
    assert win.tabs.document_count() == 1
    win.close()
