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
