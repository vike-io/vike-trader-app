"""End-to-end *user simulation* for the News space (``ui.news.NewsTab``).

This drives the real ``NewsTab`` widget the way a user would — open the space, let a
batch of headlines land, read one, narrow with the Provider / Category / Market filter
pills, type in the search box, toggle "Follow chart" to scope to the chart symbol, and
close/re-open the reader — then asserts on OBSERVABLE widget state (the rendered list,
the reader's title/source/body/chips labels, the status line, the visible/hidden reader).

No network: ``NewsTab`` polls feeds on a background ``_NewsWorker`` that is only spawned by
``start_feed()`` (never called here). We feed headlines straight into the UI-thread merge
slot ``on_items_received(...)`` — exactly what the worker's ``itemsReceived`` signal does —
so everything stays synchronous, on the main thread, and offline.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.data.news.classify import classify  # noqa: E402
from vike_trader_app.data.news.models import NewsItem  # noqa: E402
from vike_trader_app.ui.news import NewsTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# A realistic, mixed feed: distinct markets, sources, categories and a clear newest item.
# published_ms increases with the list so we can assert newest-first ordering deterministically.
def _feed():
    return [
        NewsItem(id="n1", title="Bitcoin rallies above $70k as ETF inflows surge",
                 url="https://news/btc", summary="BTC posts a fresh high on heavy demand.",
                 source="CoinDesk", market="crypto", published_ms=5000,
                 symbols=("BTC",), tags=("markets",)),
        NewsItem(id="n2", title="Ethereum upgrade ships on schedule",
                 url="https://news/eth", summary="The network completes its latest hard fork.",
                 source="Cointelegraph", market="crypto", published_ms=4000,
                 symbols=("ETH",), tags=("tech",)),
        NewsItem(id="n3", title="EUR/USD slips as the dollar firms",
                 url="https://news/eur", summary="The euro eases into the close.",
                 source="FXStreet", market="forex", published_ms=3000),
        NewsItem(id="n4", title="Apple Q3 earnings beat estimates on services growth",
                 url="https://news/aapl", summary="Apple tops profit expectations.",
                 source="CNBC", market="stocks", published_ms=2000,
                 symbols=("AAPL",)),
        NewsItem(id="n5", title="Fed signals a rate cut path into year-end",
                 url="https://news/fed", summary="Inflation cooling opens the door.",
                 source="MarketWatch", market="global", published_ms=1000),
    ]


def _titles_in_list(tab):
    """Headlines currently rendered as real (selectable) rows, top to bottom."""
    out = []
    for row in range(tab._list.count()):
        it = tab._list.item(row).data(QtCore.Qt.UserRole)
        if isinstance(it, NewsItem):
            out.append(it.title)
    return out


def test_news_user_journey(app):
    """A single, end-to-end walk through the News space as a real user would do it."""
    tab = NewsTab()
    # Show + size the widget so the splitter/list have a real geometry (no network either way).
    tab.resize(1400, 800)
    tab.show()
    app.processEvents()

    # --- 1) Headlines land (as the background worker's signal would deliver them) ---
    feed = _feed()
    tab.on_items_received(feed)
    app.processEvents()

    # The list populated with all five real rows, newest-first.
    assert _titles_in_list(tab) == [
        "Bitcoin rallies above $70k as ETF inflows surge",
        "Ethereum upgrade ships on schedule",
        "EUR/USD slips as the dollar firms",
        "Apple Q3 earnings beat estimates on services growth",
        "Fed signals a rate cut path into year-end",
    ]
    # Status line reports the honest total.
    assert "5 headlines" in tab._status.text()

    # --- 2) The newest headline auto-opens in the reader (TV behaviour) ---
    assert not tab._reader.isHidden()
    assert tab._list.currentRow() == 0
    assert "Bitcoin rallies above $70k" in tab._title.text()
    assert tab._source_lbl.text() == "CoinDesk"
    assert "fresh high" in tab._body.toPlainText()
    assert tab._open_btn.isEnabled()               # it has a url → "Open original" is active
    assert tab._chips.isVisible() and tab._chips.text()   # topic chips rendered

    # --- 3) User reads a different headline (selects the Apple row) ---
    apple_row = _titles_in_list(tab).index("Apple Q3 earnings beat estimates on services growth")
    tab._list.setCurrentRow(apple_row)
    app.processEvents()
    assert tab._current_item().id == "n4"
    assert "Apple Q3 earnings" in tab._title.text()
    assert tab._source_lbl.text() == "CNBC"
    assert "profit expectations" in tab._body.toPlainText()

    # --- 4) Filter by Provider pill → only CoinDesk rows remain ---
    tab._provider.set_selected({"CoinDesk"})
    app.processEvents()
    assert _titles_in_list(tab) == ["Bitcoin rallies above $70k as ETF inflows surge"]
    assert "1 of 5 headlines" in tab._status.text()
    tab._provider.set_selected(set())              # clear the provider constraint
    app.processEvents()
    assert len(_titles_in_list(tab)) == 5

    # --- 5) Filter by Category pill → derived classifier keeps only "Earnings" (the Apple item) ---
    # Sanity: the classifier really buckets the Apple headline as Earnings.
    assert classify(feed[3]) == "Earnings"
    tab._category.set_selected({"Earnings"})
    app.processEvents()
    assert _titles_in_list(tab) == ["Apple Q3 earnings beat estimates on services growth"]
    tab._category.set_selected(set())
    app.processEvents()
    assert len(_titles_in_list(tab)) == 5

    # --- 6) Filter by Market pill → only crypto rows remain ---
    tab._market.set_selected({"Crypto"})
    app.processEvents()
    assert set(_titles_in_list(tab)) == {
        "Bitcoin rallies above $70k as ETF inflows surge",
        "Ethereum upgrade ships on schedule",
    }
    tab._market.set_selected(set())
    app.processEvents()
    assert len(_titles_in_list(tab)) == 5

    # --- 7) Search box narrows by free text (case-insensitive substring) ---
    tab._search.setText("ethereum")
    app.processEvents()
    assert _titles_in_list(tab) == ["Ethereum upgrade ships on schedule"]
    tab._search.clear()
    app.processEvents()
    assert len(_titles_in_list(tab)) == 5

    # --- 8) "Follow chart" scopes the feed to the active symbol ---
    assert tab._follow.isChecked()                 # default ON
    tab.set_symbol("BTCUSDT")
    app.processEvents()
    # Only the BTC headline mentions Bitcoin / carries the BTC symbol.
    assert _titles_in_list(tab) == ["Bitcoin rallies above $70k as ETF inflows surge"]

    # Switch the chart symbol to ETH → the feed follows.
    tab.set_symbol("ETHUSDT")
    app.processEvents()
    assert _titles_in_list(tab) == ["Ethereum upgrade ships on schedule"]

    # Turning Follow off drops the symbol constraint → all five return.
    tab._follow.setChecked(False)
    app.processEvents()
    assert len(_titles_in_list(tab)) == 5

    # --- 9) Close the reader (TV's X) → list goes full-width; re-clicking a row reopens it ---
    tab._list.setCurrentRow(0)
    app.processEvents()
    assert not tab._reader.isHidden()
    tab.close_reader()
    assert tab._reader.isHidden()
    tab._list.itemClicked.emit(tab._list.item(0))  # a click re-opens the reader
    app.processEvents()
    assert not tab._reader.isHidden()

    tab.close()


def test_news_second_batch_dedupes_and_re_sorts(app):
    """A later poll delivers an update + a newer item: merge dedupes by id and re-sorts."""
    tab = NewsTab()
    tab.on_items_received(_feed())
    app.processEvents()
    assert len(_titles_in_list(tab)) == 5

    # Second batch: an *edited* version of the existing BTC item (same id) + a brand-new,
    # newest-of-all headline. merge() unions by id (incoming wins) and re-sorts newest-first.
    second = [
        NewsItem(id="n1", title="Bitcoin rallies above $72k (updated)",
                 url="https://news/btc", summary="Revised: BTC extends the move.",
                 source="CoinDesk", market="crypto", published_ms=5500, symbols=("BTC",)),
        NewsItem(id="n6", title="Solana breaks out to a multi-month high",
                 url="https://news/sol", summary="SOL leads the majors.",
                 source="Decrypt", market="crypto", published_ms=9000, symbols=("SOL",)),
    ]
    tab.on_items_received(second)
    app.processEvents()

    titles = _titles_in_list(tab)
    assert len(titles) == 6                         # 5 + 1 new (n1 was replaced, not duplicated)
    assert titles[0] == "Solana breaks out to a multi-month high"   # newest now leads
    # The edited BTC headline replaced the old one (no stale "$70k" row survives).
    assert "Bitcoin rallies above $72k (updated)" in titles
    assert "Bitcoin rallies above $70k as ETF inflows surge" not in titles
    assert "6 headlines" in tab._status.text()

    tab.close()


def test_news_empty_filter_shows_actionable_placeholder(app):
    """An over-constrained filter yields an explanatory, non-selectable placeholder row."""
    tab = NewsTab()
    tab.on_items_received(_feed())
    app.processEvents()

    # Follow BTC but constrain Market to Forex → no headline can satisfy both.
    tab._follow.setChecked(True)
    tab.set_symbol("BTCUSDT")
    tab._market.set_selected({"Forex"})
    app.processEvents()

    # Honest status ("0 of 5"), not a misleading "5 headlines" over an empty list.
    assert "0 of 5" in tab._status.text()
    # Exactly one row: a non-selectable placeholder that explains the Follow-chart scoping.
    assert tab._list.count() == 1
    ph = tab._list.item(0)
    assert ph.data(QtCore.Qt.UserRole) is None     # placeholder, not a NewsItem
    assert "Follow chart" in ph.text() and "Forex" in ph.text()
    tab._list.setCurrentRow(0)
    app.processEvents()
    assert tab._current_item() is None             # the placeholder cannot be selected

    # Follow the hint (turn Follow off) → the Forex headline appears as a real, selectable row.
    tab._follow.setChecked(False)
    app.processEvents()
    rows = _titles_in_list(tab)
    assert rows == ["EUR/USD slips as the dollar firms"]

    tab.close()
