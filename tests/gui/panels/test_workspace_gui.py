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
    f1 = win.open_tool("screener"); app.processEvents()      # MT-style: opens as its own window
    assert win._tool_frames.get("screener") is f1
    f2 = win.open_tool("screener")           # singleton: re-open focuses the same window
    assert f2 is f1
    win.close()


def test_keys_match_factories(app):
    assert set(ToolRegistry.keys()) == set(ToolRegistry.factories())


def test_no_spaces_after_chart_unify(app):
    # chart-unify keystone: the central Chart space is GONE — there are now ZERO docked spaces.
    # Charts are floating ChartWindowFrame peers; every tool (incl. Studio) is an on-demand window.
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    assert win.tabs.count() == 0                        # no spaces at all (central chart retired)
    assert win._SPACE_ITEMS == []
    assert win._chart_space_dock() is None
    for attr in ("screener", "journal", "alerts", "datamanager",
                 "news", "calendar_space", "options", "studio"):
        assert getattr(win, attr, None) is None        # all 8 tools no longer eager
    win.close()


def test_each_tool_opens_and_closes_as_window(app):
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    for key in ToolRegistry.keys():                     # incl. studio (the 8th tool now)
        frame = win.open_tool(key); app.processEvents()  # MT-style: each tool is its own window
        assert win._tool_frames.get(key) is frame
        # legacy attr is set while open
        attr = {"data": "datamanager", "calendar": "calendar_space"}.get(key, key)
        assert getattr(win, attr, None) is not None
        frame.close_window(); app.processEvents()
        assert getattr(win, attr, None) is None        # cleared on close (no leak)
        assert key not in win._tool_frames
    win.close()


def test_studio_opens_and_closes_as_window(app):
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    assert win.studio is None and win.studio_price is None      # not eager anymore
    assert win.tabs.count() == 0                                 # no spaces (central chart retired)
    frame = win.open_tool("studio"); app.processEvents()
    assert win._tool_frames.get("studio") is frame
    assert win.studio is not None and win.studio_price is not None
    frame.close_window(); app.processEvents()
    assert win.studio is None and win.studio_price is None       # cleared on close (no dangling)
    win.close()


def test_backtest_pipeline_safe_when_studio_closed(app):
    # load_bars / replay must not crash when Studio is closed (studio_price is None).
    # chart-unify keystone: there is NO central chart in the pipeline, so with Studio closed the
    # pipeline is EMPTY (_pipeline_charts() == []) — the backtest runs and feeds nothing, no crash.
    from vike_trader_app.core.model import Bar
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    bars = [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(40)]
    win.load_bars(bars, record=False)   # studio_price is None -> _pipeline_charts() is empty
    app.processEvents()                 # must not raise
    win._render_frame()                 # replay render with Studio closed
    assert win._pipeline_charts() == []   # no central chart; Studio closed -> empty pipeline
    win.close()


def test_controls_survive_studio_close(app):
    # The replay controls (self.slider etc.) are built eagerly and re-parented OUT of the Studio
    # dock on close (_rescue_studio_controls). load_bars touches self.slider unconditionally, so a
    # rescued-but-functional slider must survive a Studio close (guards the 0xC0000409 class).
    from vike_trader_app.core.model import Bar
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    frame = win.open_tool("studio"); app.processEvents()
    frame.close_window(); app.processEvents()
    assert win.studio is None and win.studio_price is None
    bars = [Bar(ts=i * 60_000, open=100.0 + i, high=101.0 + i, low=99.0 + i, close=100.0 + i)
            for i in range(40)]
    win.load_bars(bars, record=False)          # must NOT crash on the rescued slider
    assert win.slider.maximum() == len(bars) - 1
    win.close()


def test_options_close_reopen_no_stale_signals(app):
    # Regression: closing Options must drop the OptionsService->tab signal connections so an
    # in-flight fetch worker can't emit into the DeleteOnClose-destroyed tab (segfault class).
    # Re-opening must re-wire cleanly without a double-disconnect RuntimeWarning.
    win = MainWindow(session_path=None); win.show(); app.processEvents()
    f1 = win.open_tool("options"); app.processEvents()
    f1.close_window(); app.processEvents()
    assert getattr(win, "options", None) is None
    assert win._options_wired is False                 # disconnected on close, re-armed on open
    f2 = win.open_tool("options"); app.processEvents()  # must not warn/crash
    assert win._tool_frames.get("options") is f2
    win.close()


def test_open_tools_persist_across_restart(app, tmp_path):
    p = str(tmp_path / "s.json")
    w1 = MainWindow(session_path=p); w1.show(); app.processEvents()
    w1.open_tool("screener"); w1.open_tool("news"); app.processEvents()
    w1.close(); app.processEvents()                       # closeEvent saves the session
    w2 = MainWindow(session_path=p); w2.show(); app.processEvents()
    # MT-style: tools persist + restore as their own windows (in _tool_frames), not docks
    assert "screener" in w2._tool_frames and "news" in w2._tool_frames
    # legacy aliases re-bound on restore
    assert getattr(w2, "screener", None) is not None
    assert getattr(w2, "news", None) is not None
    w2.close()


def test_empty_session_restores_no_tool_docks(app, tmp_path):
    p = str(tmp_path / "s.json")
    w1 = MainWindow(session_path=p); w1.show(); app.processEvents()
    w1.close(); app.processEvents()
    w2 = MainWindow(session_path=p); w2.show(); app.processEvents()
    names = [d.objectName() for d in w2.dock_manager.dockWidgetsMap().values()]
    assert not any(n.startswith("tool:") for n in names)
    w2.close()
