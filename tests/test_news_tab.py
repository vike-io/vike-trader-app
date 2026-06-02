import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.news.feeds_store import SavedFeedStore  # noqa: E402
from vike_trader_app.data.news.models import NewsItem  # noqa: E402
from vike_trader_app.ui.news import NewsTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _items():
    return [
        NewsItem(id="1", title="BTC soars", url="https://x/1", summary="up",
                 source="CoinDesk", market="crypto", published_ms=2000, symbols=("BTC",)),
        NewsItem(id="2", title="EUR dips", url="https://x/2", summary="down",
                 source="FXStreet", market="forex", published_ms=1000),
    ]


def test_tab_populates_and_reader_renders(app, tmp_path):
    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received(_items())
    assert tab._list.count() == 2
    tab._list.setCurrentRow(0)                       # newest first → "BTC soars"
    assert "BTC soars" in tab._title.text()
    assert tab._current_item().url == "https://x/1"


def test_market_filter_reduces_list(app, tmp_path):
    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received(_items())
    tab._market.setCurrentText("Crypto")             # currentTextChanged → _refresh_list
    assert tab._list.count() == 1


def test_save_and_apply_feed(app, tmp_path):
    store = SavedFeedStore(str(tmp_path / "f.json"))
    tab = NewsTab(store=store)
    tab.on_items_received(_items())
    tab._market.setCurrentText("Forex")
    tab._save_feed_named("FX only")                  # programmatic save (no modal)
    assert "FX only" in [f.name for f in store.feeds()]
    tab._market.setCurrentText("All")
    tab._apply_saved("FX only")
    assert tab._market.currentText() == "Forex" and tab._list.count() == 1


def test_set_symbol_with_follow_filters(app, tmp_path):
    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received(_items())
    tab._follow.setChecked(True)
    tab.set_symbol("BTCUSDT")
    assert tab._list.count() == 1                    # only the BTC item matches
