import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
pytest.importorskip("PySide6"); pytest.importorskip("PySide6QtAds")
import PySide6QtAds as QtAds  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402
from vike_trader_app.ui.toolreg import ToolRegistry, make_tool_dock  # noqa: E402
from vike_trader_app.ui.dockshell import configure_dock_manager_defaults  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_registry_lists_tool_keys(app):
    assert {"screener", "journal", "alerts", "data", "news", "calendar",
            "options", "studio"} <= set(ToolRegistry.keys())


def test_make_tool_dock_wraps_widget(app):
    configure_dock_manager_defaults()
    host = QtWidgets.QMainWindow()
    mgr = QtAds.CDockManager(host)
    w = QtWidgets.QLabel("x")
    dock = make_tool_dock(mgr, "screener", w)
    assert isinstance(dock, QtAds.CDockWidget)
    assert dock.objectName() == "tool:screener"
    assert dock.widget() is w
    host.deleteLater()


from vike_trader_app.ui.app import MainWindow  # noqa: E402


def test_open_tool_opens_then_focuses(app):
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    d1 = win.open_tool("screener"); app.processEvents()
    assert d1.objectName() == "tool:screener" and not d1.isClosed()
    d2 = win.open_tool("screener")           # singleton: re-open focuses the same dock
    assert d2 is d1
    win.close()


def test_keys_match_factories(app):
    assert set(ToolRegistry.keys()) == set(ToolRegistry.factories())


def test_only_chart_and_studio_remain_spaces(app):
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    assert win.tabs.count() == 2                      # Chart + Studio only
    for attr in ("screener", "journal", "alerts", "datamanager",
                 "news", "calendar_space", "options"):
        assert getattr(win, attr, None) is None       # 7 tools no longer eager
    win.close()


def test_each_tool_opens_and_closes_as_dock(app):
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    for key in ToolRegistry.keys():
        if key == "studio":
            continue                                   # studio stays a space this increment
        dock = win.open_tool(key); app.processEvents()
        assert dock.objectName() == f"tool:{key}"
        # legacy attr is set while open
        attr = {"data": "datamanager", "calendar": "calendar_space"}.get(key, key)
        assert getattr(win, attr, None) is not None
        dock.closeDockWidget(); app.processEvents()
        assert getattr(win, attr, None) is None        # cleared on close (no leak)
    win.close()
