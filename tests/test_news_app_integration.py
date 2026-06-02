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


def test_news_space_present_and_rail_aligned(app):
    win = MainWindow()
    try:
        names = [name for _glyph, name in win._RAIL_ITEMS]
        assert "News" in names
        # the rail drives tabs by position, so News's tab index must equal its rail index
        assert win.tabs.indexOf(win.news) == names.index("News")
        assert isinstance(win.news, NewsTab)
    finally:
        win.close()


def test_set_symbol_forwards_to_news(app):
    win = MainWindow()
    try:
        win.news.set_symbol("ETHUSDT")
        assert win.news._symbol == "ETHUSDT"
    finally:
        win.close()
