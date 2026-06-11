"""Offscreen tests for named workspaces (Phase 4): apply built-ins, save/switch, persistence.

load_symbol_bars is monkeypatched to synthetic bars; live is disabled suite-wide.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtWidgets  # noqa: E402

import vike_trader_app.ui.chartdoc as chartdoc  # noqa: E402
from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.dataload import LoadResult  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _synthetic_load(monkeypatch):
    bars = [Bar(ts=i * 60_000, open=100 + i, high=101 + i, low=99 + i, close=100 + i)
            for i in range(40)]
    monkeypatch.setattr(chartdoc, "load_symbol_bars", lambda *a, **k: LoadResult(list(bars)))


def test_builtin_workspaces_listed(app):
    win = MainWindow(session_path=None)
    assert win._workspaces.names()[:3] == ["Trading", "Research", "Backtesting"]
    win.close()


def test_apply_research_opens_two_linked_docs(app):
    win = MainWindow(session_path=None)
    assert win._apply_workspace("Research")
    assert [d.symbol for d in win._doc_widgets] == ["ETHUSDT", "SOLUSDT"]
    assert all(d.link_group == 3 for d in win._doc_widgets)
    assert win._watchlist_link == 3
    win.close()


def test_apply_trading_clears_documents(app):
    win = MainWindow(session_path=None)
    win._apply_workspace("Research")
    assert len(win._chart_frames) == 2
    win._apply_workspace("Trading")              # Trading has no documents
    assert len(win._chart_frames) == 0
    win.close()


def test_apply_backtesting_selects_studio_space(app):
    win = MainWindow(session_path=None)
    win._apply_workspace("Backtesting")
    assert win.tabs.currentWidget() is win.studio
    win.close()


def test_save_and_switch_user_workspace(app):
    win = MainWindow(session_path=None)
    win._new_chart_document("ADAUSDT", "4h")
    win._new_chart_document("DOTUSDT", "1h")
    assert win._save_workspace_as("Mine")
    assert win._workspaces.is_user("Mine")

    win._apply_workspace("Trading")              # wipe to 0 docs
    assert len(win._chart_frames) == 0
    win._apply_workspace("Mine")                 # restore the 2 saved docs
    assert [d.symbol for d in win._doc_widgets] == ["ADAUSDT", "DOTUSDT"]
    win.close()


def test_unknown_workspace_is_noop(app):
    win = MainWindow(session_path=None)
    assert win._apply_workspace("Nope") is False
    win.close()


def test_workspace_persists_across_windows(app, tmp_path):
    session = tmp_path / "session.json"
    first = MainWindow(session_path=str(session))
    first._new_chart_document("ETHUSDT", "2h")
    first._save_workspace_as("Desk")
    first.close()

    # the store wrote workspaces.json next to the session file
    ws_file = tmp_path / "workspaces.json"
    assert ws_file.exists()
    assert "Desk" in json.loads(ws_file.read_text(encoding="utf-8"))["workspaces"]

    second = MainWindow(session_path=str(session))
    assert "Desk" in second._workspaces.names()
    assert second._apply_workspace("Desk")
    assert [d.symbol for d in second._doc_widgets] == ["ETHUSDT"]
    second.close()


def test_delete_user_workspace(app):
    win = MainWindow(session_path=None)
    win._save_workspace_as("Temp")
    assert "Temp" in win._workspaces.names()
    assert win._delete_workspace("Temp")
    assert "Temp" not in win._workspaces.names()
    win.close()


def test_workspaces_menu_populates(app):
    win = MainWindow(session_path=None)
    menu = QtWidgets.QMenu()
    win._populate_workspaces_menu(menu)
    labels = [a.text() for a in menu.actions() if a.text()]
    assert any("Trading" in t for t in labels)
    assert any("Save current as" in t for t in labels)
    win.close()
