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
    tab._market.set_selected({"Crypto"})             # TV multi-select dropdown → _refresh_list
    assert tab._list.count() == 1


def test_category_filter_reduces_list(app, tmp_path):
    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received([
        NewsItem(id="a", title="Apple Q3 earnings beat estimates", url="u1", summary="",
                 source="CNBC", market="stocks", published_ms=3000),
        NewsItem(id="b", title="Bitcoin rallies above $70k", url="u2", summary="",
                 source="CoinDesk", market="crypto", published_ms=2000),
    ])
    tab._category.set_selected({"Earnings"})         # derived classifier → only the Apple item
    assert tab._list.count() == 1
    assert "Apple" in tab._list.item(0).data(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.UserRole).title


def test_close_reader_then_row_reopens(app, tmp_path):
    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received(_items())
    assert not tab._reader.isHidden()                # reader open by default
    tab.close_reader()                               # TV's X button
    assert tab._reader.isHidden()                    # list goes full-width
    tab._list.setCurrentRow(0)                        # clicking a headline reopens the reader
    assert not tab._reader.isHidden()
    assert "BTC soars" in tab._title.text()


def test_set_symbol_with_follow_filters(app, tmp_path):
    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received(_items())
    tab._follow.setChecked(True)
    tab.set_symbol("BTCUSDT")
    assert tab._list.count() == 1                    # only the BTC item matches


def test_empty_filter_shows_actionable_placeholder_and_honest_count(app, tmp_path):
    from PySide6 import QtCore

    tab = NewsTab(store=SavedFeedStore(str(tmp_path / "f.json")))
    tab.on_items_received(_items())                  # 1 crypto(BTC) + 1 forex
    tab._follow.setChecked(True)
    tab.set_symbol("BTCUSDT")
    tab._market.set_selected({"Forex"})              # Forex AND the BTC symbol = nothing

    # honest count — not a misleading "2 headlines" while the list is empty
    assert "0 of 2" in tab._status.text()
    # one non-selectable placeholder explains *why* (the Follow-chart scoping)
    assert tab._list.count() == 1
    ph = tab._list.item(0)
    assert ph.data(QtCore.Qt.UserRole) is None       # placeholder, not a NewsItem
    assert "Follow chart" in ph.text() and "Forex" in ph.text()
    tab._list.setCurrentRow(0)
    assert tab._current_item() is None               # placeholder can't be selected

    # following the hint (turn off Follow chart) reveals the Forex item
    tab._follow.setChecked(False)
    assert tab._list.count() == 1
    assert tab._list.item(0).data(QtCore.Qt.UserRole) is not None   # a real item now, no placeholder
