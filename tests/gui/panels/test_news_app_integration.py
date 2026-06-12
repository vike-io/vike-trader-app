import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.app import MainWindow  # noqa: E402
from vike_trader_app.ui.news import NewsTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_news_tool_opens_as_dock(app):
    # News is now an on-demand TOOL (a dock), not a space: it lives in _TOOL_ITEMS keyed "news"
    # and is built/opened via open_tool, which mirrors the live tab onto win.news while open.
    win = MainWindow()
    try:
        assert "news" in [key for _glyph, _name, key in win._TOOL_ITEMS]
        assert getattr(win, "news", None) is None     # not eager
        dock = win.open_tool("news")
        assert dock.objectName() == "tool:news"
        assert isinstance(win.news, NewsTab)
    finally:
        win.close()


def test_set_symbol_forwards_to_news(app):
    win = MainWindow()
    try:
        win.open_tool("news")                          # build the News dock (sets win.news)
        win.news.set_symbol("ETHUSDT")
        assert win.news._symbol == "ETHUSDT"
    finally:
        win.close()
