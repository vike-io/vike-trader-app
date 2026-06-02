"""News space — TradingView News-Flow-style feed: filter toolbar + (list | reader) split.

Free Tier-0 RSS/JSON sources only (see data/news/providers.py). A background _NewsWorker polls
off the UI thread and pushes batches via a signal; the aggregator merges them into the model on
the UI thread. Pure network → safe off-thread (no Parquet/Catalog reads here).
"""

from __future__ import annotations

import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

from ..data.news.aggregator import apply_filter, merge
from ..data.news.fetch import fetch_all
from ..data.news.feeds_store import SavedFeed, SavedFeedStore
from ..data.news.models import NewsFilter, NewsItem
from ..data.news.providers import PROVIDERS
from . import theme

_MARKETS = {"All": None, "Crypto": "crypto", "Forex": "forex", "Stocks": "stocks"}


class _NewsWorker(QtCore.QThread):
    """Polls all enabled providers off the UI thread; emits parsed batches."""

    itemsReceived = QtCore.Signal(object)   # list[NewsItem]
    failed = QtCore.Signal(str)

    def __init__(self, specs, symbol, *, follow=True, poll_seconds=60.0, parent=None):
        super().__init__(parent)
        self._specs = list(specs)
        self._symbol = symbol
        self._follow = follow
        self._poll = poll_seconds
        self._stop = False
        self._wake = threading.Event()

    def set_symbol(self, symbol):
        self._symbol = symbol
        self._wake.set()

    def set_follow(self, follow):
        self._follow = follow
        self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def run(self):
        while not self._stop:
            try:
                sym = self._symbol if self._follow else None
                items = fetch_all(self._specs, sym)
                if not self._stop:
                    self.itemsReceived.emit(items)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI thread
                self.failed.emit(str(exc))
            self._wake.wait(self._poll)
            self._wake.clear()


class NewsTab(QtWidgets.QWidget):
    """Filter toolbar + list/reader split over a merged, deduped, time-sorted news feed."""

    def __init__(self, store: SavedFeedStore | None = None, providers=None,
                 parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._store = store if store is not None else SavedFeedStore()
        self._providers = list(providers) if providers is not None else list(PROVIDERS)
        self._items: list[NewsItem] = []
        self._symbol: str | None = None
        self._worker: _NewsWorker | None = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(self._build_toolbar())

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._list = QtWidgets.QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentRowChanged.connect(self._on_row_changed)
        split.addWidget(self._list)
        split.addWidget(self._build_reader())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        root.addWidget(self._status)
        self._reload_feed_combo()

    # ---- construction helpers ----
    def _build_toolbar(self) -> QtWidgets.QHBoxLayout:
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)

        self._market = QtWidgets.QComboBox()
        self._market.addItems(list(_MARKETS.keys()))
        self._market.currentTextChanged.connect(lambda _t: self._refresh_list())

        self._provider_btn = QtWidgets.QToolButton()
        self._provider_btn.setText("Providers")
        self._provider_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QtWidgets.QMenu(self._provider_btn)
        self._provider_actions: dict[str, QtGui.QAction] = {}
        for name in sorted({p.name for p in self._providers}):
            act = menu.addAction(name)
            act.setCheckable(True)
            act.toggled.connect(lambda _c: self._refresh_list())
            self._provider_actions[name] = act
        self._provider_btn.setMenu(menu)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search headlines…")
        self._search.textChanged.connect(lambda _t: self._refresh_list())

        self._follow = QtWidgets.QToolButton()
        self._follow.setText("⌖ Follow chart")
        self._follow.setCheckable(True)
        self._follow.setChecked(True)                 # default ON (chart-centric app)
        self._follow.toggled.connect(self._on_follow_toggled)

        self._feed_combo = QtWidgets.QComboBox()
        self._feed_combo.currentTextChanged.connect(self._on_feed_combo)
        self._save_btn = QtWidgets.QPushButton("Save feed")
        self._save_btn.clicked.connect(self._save_feed)
        self._del_btn = QtWidgets.QPushButton("Delete")
        self._del_btn.clicked.connect(self._delete_feed)
        self._refresh_btn = QtWidgets.QPushButton("↻")
        self._refresh_btn.clicked.connect(lambda: self._worker and self._worker.refresh_now())

        for w in (QtWidgets.QLabel("Market:"), self._market, self._provider_btn,
                  self._search, self._follow, QtWidgets.QLabel("Feed:"), self._feed_combo,
                  self._save_btn, self._del_btn, self._refresh_btn):
            bar.addWidget(w)
        bar.setStretchFactor(self._search, 1)
        return bar

    def _build_reader(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(10, 6, 6, 6)
        self._title = QtWidgets.QLabel("Select a headline")
        self._title.setWordWrap(True)
        self._title.setStyleSheet(f"color:{theme.TEXT};font-size:15px;font-weight:700;")
        self._meta = QtWidgets.QLabel("")
        self._meta.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        self._body = QtWidgets.QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._open_btn = QtWidgets.QPushButton("↗ Open original")
        self._open_btn.clicked.connect(self._open_original)
        self._open_btn.setEnabled(False)
        for x in (self._title, self._meta, self._body, self._open_btn):
            v.addWidget(x)
        v.setStretchFactor(self._body, 1)
        return w

    # ---- feed lifecycle ----
    def start_feed(self, symbol: str | None = None) -> None:
        """Lazily start the background poller (called when the News space is first opened)."""
        if symbol is not None:
            self._symbol = symbol
        if self._worker is not None:
            return
        self._worker = _NewsWorker(self._providers, self._symbol, follow=self._follow.isChecked())
        self._worker.itemsReceived.connect(self.on_items_received)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def stop_feed(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.wait(2000)
            self._worker = None

    def set_symbol(self, symbol: str | None) -> None:
        self._symbol = symbol
        if self._worker is not None:
            self._worker.set_symbol(symbol)
        if self._follow.isChecked():
            self._refresh_list()

    # ---- slots ----
    def on_items_received(self, items) -> None:
        self._items = merge(self._items, list(items))
        self._refresh_list()
        self._status.setText(f"{len(self._items)} headlines · updated {time.strftime('%H:%M:%S')}")

    def _on_failed(self, message: str) -> None:
        self._status.setText(f"Feed error: {message}")     # status line, never a modal

    def _on_follow_toggled(self, on: bool) -> None:
        if self._worker is not None:
            self._worker.set_follow(on)
        self._refresh_list()

    def _on_row_changed(self, _row: int) -> None:
        it = self._current_item()
        if it is None:
            return
        self._title.setText(it.title)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(it.published_ms / 1000)) if it.published_ms else ""
        self._meta.setText(f"{it.source} · {when}" + (f" · {', '.join(it.symbols)}" if it.symbols else ""))
        self._body.setPlainText(it.summary or "(no summary — open the original)")
        self._open_btn.setEnabled(bool(it.url))

    def _open_original(self) -> None:
        it = self._current_item()
        if it and it.url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(it.url))

    # ---- filter + list ----
    def _current_filter(self) -> NewsFilter:
        provs = frozenset(n for n, a in self._provider_actions.items() if a.isChecked())
        sym = self._symbol if self._follow.isChecked() else None
        return NewsFilter(market=_MARKETS[self._market.currentText()],
                          providers=provs, symbol=sym, query=self._search.text().strip())

    def _refresh_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for it in apply_filter(self._items, self._current_filter()):
            when = time.strftime("%H:%M", time.localtime(it.published_ms / 1000)) if it.published_ms else "--:--"
            chip = f"  [{it.symbols[0]}]" if it.symbols else ""
            qi = QtWidgets.QListWidgetItem(f"{when}  {it.source} — {it.title}{chip}")
            qi.setData(QtCore.Qt.UserRole, it)
            self._list.addItem(qi)
        self._list.blockSignals(False)

    def _current_item(self) -> NewsItem | None:
        qi = self._list.currentItem()
        return qi.data(QtCore.Qt.UserRole) if qi is not None else None

    # ---- saved feeds ----
    def _reload_feed_combo(self) -> None:
        self._feed_combo.blockSignals(True)
        self._feed_combo.clear()
        self._feed_combo.addItem("— saved feeds —")
        for f in self._store.feeds():
            self._feed_combo.addItem(f.name)
        self._feed_combo.blockSignals(False)

    def _save_feed(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Save feed", "Name:")   # user-initiated → modal OK
        if ok and name.strip():
            self._save_feed_named(name.strip())

    def _save_feed_named(self, name: str) -> None:
        f = self._current_filter()
        self._store.add(SavedFeed(
            name=name,
            market=self._market.currentText() if f.market else "",
            providers=sorted(f.providers),
            symbol=self._symbol or "",
            query=f.query,
            follow_chart=self._follow.isChecked(),
        ))
        self._reload_feed_combo()
        self._feed_combo.setCurrentText(name)

    def _on_feed_combo(self, name: str) -> None:
        if name and not name.startswith("—"):
            self._apply_saved(name)

    def _apply_saved(self, name: str) -> None:
        feed = next((f for f in self._store.feeds() if f.name == name), None)
        if feed is None:
            return
        self._market.setCurrentText(feed.market or "All")
        for n, act in self._provider_actions.items():
            act.setChecked(n in feed.providers)
        self._search.setText(feed.query)
        self._follow.setChecked(feed.follow_chart)
        self._refresh_list()

    def _delete_feed(self) -> None:
        name = self._feed_combo.currentText()
        if name and not name.startswith("—"):
            self._store.remove(name)
            self._reload_feed_combo()
