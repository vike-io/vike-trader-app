"""News space — TradingView News-Flow-style feed: filter toolbar + (list | reader) split.

Free Tier-0 RSS/JSON sources only (see data/news/providers.py). A background _NewsWorker polls
off the UI thread and pushes batches via a signal; the aggregator merges them into the model on
the UI thread. Pure network → safe off-thread (no Parquet/Catalog reads here).
"""

from __future__ import annotations

import html
import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

from ..data.news.aggregator import apply_filter, merge
from ..data.news.fetch import fetch_iter
from ..data.news.feeds_store import SavedFeed, SavedFeedStore
from ..data.news.models import NewsFilter, NewsItem
from ..data.news.providers import PROVIDERS
from . import icons, theme

_MARKETS = {"All": None, "Crypto": "crypto", "Forex": "forex", "Stocks": "stocks"}

# Deterministic provider-badge palette (no real logos — colored initials, like Market watch).
_AV_COLORS = ["#3fe08a", "#f0a93f", "#3f9be0", "#e0643f", "#b06fe0", "#3fe0c8", "#e03f8a", "#9be03f"]
_av_cache: dict[str, "QtGui.QPixmap"] = {}


def _avatar_for(source: str, size: int = 30) -> "QtGui.QPixmap":
    """A small circular provider badge (first initial on a stable per-source colour)."""
    key = f"{source}@{size}"
    pm = _av_cache.get(key)
    if pm is None:
        color = _AV_COLORS[sum(map(ord, source)) % len(_AV_COLORS)]
        pm = icons.avatar((source or "?")[:1].upper(), color).scaled(
            size, size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        _av_cache[key] = pm
    return pm


def _ago(ms: int) -> str:
    """Relative timestamp like TradingView ('just now', '5m ago', '3h ago', '2d ago')."""
    if not ms:
        return ""
    secs = max(0, int(time.time() * 1000) - ms) // 1000
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


class _NewsRowDelegate(QtWidgets.QStyledItemDelegate):
    """TradingView-style two-line row: provider badge · source · relative time, then headline.

    Matches TV news-flow's type scale: 12px muted meta, 14px DemiBold headline, generous row
    height, hairline separator inset to the text column.
    """

    ROW_H = 68
    AV = 32

    def sizeHint(self, opt, idx):
        return QtCore.QSize(opt.rect.width(), self.ROW_H)

    def paint(self, p, opt, idx):
        it = idx.data(QtCore.Qt.UserRole)
        if not isinstance(it, NewsItem):
            super().paint(p, opt, idx)           # empty-state placeholder → default text paint
            return
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = opt.rect
        if opt.state & QtWidgets.QStyle.State_Selected:
            bg = QtGui.QColor(theme.ACCENT)
            bg.setAlpha(28)
            p.fillRect(r, bg)
            p.fillRect(QtCore.QRect(r.left(), r.top(), 2, r.height()), QtGui.QColor(theme.ACCENT))
        elif opt.state & QtWidgets.QStyle.State_MouseOver:
            p.fillRect(r, QtGui.QColor(theme.RAISE))
        p.drawPixmap(r.left() + 16, r.top() + (self.ROW_H - self.AV) // 2, _avatar_for(it.source, self.AV))
        x = r.left() + 16 + self.AV + 12
        right = r.right() - 16

        meta_font = QtGui.QFont(p.font())
        meta_font.setPixelSize(12)
        meta_font.setWeight(QtGui.QFont.Weight.Normal)
        p.setFont(meta_font)
        p.setPen(QtGui.QColor(theme.TEXT3))
        chip = f"  ·  {it.symbols[0]}" if it.symbols else ""
        p.drawText(x, r.top() + 25, f"{it.source}  ·  {_ago(it.published_ms)}{chip}")

        title_font = QtGui.QFont(p.font())
        title_font.setPixelSize(14)
        title_font.setWeight(QtGui.QFont.Weight.DemiBold)   # TV headline weight (~600), not full 700
        p.setFont(title_font)
        p.setPen(QtGui.QColor(theme.TEXT))
        title = QtGui.QFontMetrics(title_font).elidedText(it.title, QtCore.Qt.ElideRight, right - x)
        p.drawText(x, r.top() + 47, title)

        sep = QtGui.QColor(theme.BORDER)
        sep.setAlpha(140)
        p.setPen(sep)
        p.drawLine(x, r.bottom(), right, r.bottom())
        p.restore()


class _NewsWorker(QtCore.QThread):
    """Polls all enabled providers off the UI thread; emits each feed's items as it lands."""

    itemsReceived = QtCore.Signal(object)   # list[NewsItem]
    failed = QtCore.Signal(str)

    def __init__(self, specs, symbol, *, follow=True, poll_seconds=60.0, parent=None):
        super().__init__(parent)
        self._specs = list(specs)
        self._symbol = symbol
        self._follow_chart = follow
        self._poll = poll_seconds
        self._stop = False
        self._wake = threading.Event()

    def set_symbol(self, symbol):
        self._symbol = symbol
        self._wake.set()

    def set_follow(self, follow):
        self._follow_chart = follow
        self._wake.set()

    def refresh_now(self):
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()

    def run(self):
        while not self._stop:
            try:
                sym = self._symbol if self._follow_chart else None
                gen = fetch_iter(self._specs, sym)
                try:
                    for chunk in gen:          # emit each feed as it completes → progressive paint
                        if self._stop:
                            break
                        self.itemsReceived.emit(chunk)
                finally:
                    gen.close()                # shut the pool down promptly if we broke out early
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
        self._list.setItemDelegate(_NewsRowDelegate(self._list))   # TV-style two-line rows
        self._list.setMouseTracking(True)                          # hover highlight
        self._list.setStyleSheet(
            f"QListWidget{{background:{theme.CHART_BG};border:none;outline:none;}}")
        self._list.currentRowChanged.connect(self._on_row_changed)
        split.addWidget(self._list)
        split.addWidget(self._build_reader())
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        root.addWidget(split, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        root.addWidget(self._status)
        self._last_update = ""
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
        v.setContentsMargins(28, 22, 28, 18)
        v.setSpacing(12)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(10)
        self._reader_av = QtWidgets.QLabel()
        self._reader_av.setFixedSize(30, 30)
        self._source_lbl = QtWidgets.QLabel("")
        self._source_lbl.setStyleSheet(f"color:{theme.TEXT};font-size:13px;font-weight:600;")
        head.addWidget(self._reader_av)
        head.addWidget(self._source_lbl)
        head.addStretch(1)
        v.addLayout(head)
        self._title = QtWidgets.QLabel("Select a headline")
        self._title.setWordWrap(True)
        self._title.setStyleSheet(
            f"color:{theme.TEXT};font-size:24px;font-weight:700;line-height:130%;")
        self._meta = QtWidgets.QLabel("")
        self._meta.setStyleSheet(f"color:{theme.TEXT3};font-size:13px;")
        self._chips = QtWidgets.QLabel("")
        self._chips.setTextFormat(QtCore.Qt.RichText)
        self._chips.setVisible(False)
        self._body = QtWidgets.QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.setStyleSheet("QTextBrowser{border:none;background:transparent;}")
        self._open_btn = QtWidgets.QPushButton("↗ Open original")
        self._open_btn.clicked.connect(self._open_original)
        self._open_btn.setEnabled(False)
        for x in (self._title, self._meta, self._chips, self._body, self._open_btn):
            v.addWidget(x)
        v.setStretchFactor(self._body, 1)
        return w

    def _chip_html(self, it: NewsItem) -> str:
        tags = ([it.market.capitalize()] if it.market else []) + list(it.symbols)
        cell = (f"<span style='background:{theme.RAISE};color:{theme.TEXT2};"
                f"padding:3px 9px;margin-right:6px;font-size:11px;'>&nbsp;{{t}}&nbsp;</span>")
        return "".join(cell.format(t=t) for t in tags)

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
            self._worker.wait(8000)   # ≥ the 6s per-feed urllib timeout, so a stalled poll still joins
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
        self._last_update = time.strftime("%H:%M:%S")
        self._refresh_list()

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
        self._reader_av.setPixmap(_avatar_for(it.source, 26))
        self._source_lbl.setText(it.source)
        self._title.setText(it.title)
        when = time.strftime("%b %d, %Y · %H:%M", time.localtime(it.published_ms / 1000)) if it.published_ms else ""
        self._meta.setText(f"{when}  ·  {_ago(it.published_ms)}" if when else "")
        self._chips.setText(self._chip_html(it))
        self._chips.setVisible(bool(it.market or it.symbols))
        summary = html.escape(it.summary or "(no summary — open the original)").replace("\n", "<br>")
        self._body.setHtml(
            f"<div style='color:{theme.TEXT2};font-size:15px;line-height:165%;'>{summary}</div>")
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
        filtered = apply_filter(self._items, self._current_filter())
        for it in filtered:
            qi = QtWidgets.QListWidgetItem(it.title)   # text is a fallback; _NewsRowDelegate paints
            qi.setData(QtCore.Qt.UserRole, it)
            self._list.addItem(qi)
        if not filtered and self._items:
            ph = QtWidgets.QListWidgetItem(self._empty_hint())   # non-selectable explainer row
            ph.setFlags(QtCore.Qt.NoItemFlags)                   # no UserRole data → _current_item stays None
            self._list.addItem(ph)
        self._list.blockSignals(False)
        self._update_status(len(filtered))

    def _empty_hint(self) -> str:
        """Explain *why* the list is empty (e.g. Follow-chart scoping) — not just a blank pane."""
        f = self._current_filter()
        scope = f"{self._market.currentText()} " if f.market else ""
        if f.symbol:
            return (f"No {scope}headlines mention {f.symbol}. "
                    f"Turn off “⊕ Follow chart” to see all {scope}news.")
        if f.query:
            return f"No headlines match “{f.query}”."
        return f"No {scope}headlines right now."

    def _update_status(self, shown: int) -> None:
        total = len(self._items)
        head = f"{total} headlines" if shown == total else f"{shown} of {total} headlines"
        self._status.setText(head + (f" · updated {self._last_update}" if self._last_update else ""))

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
        if feed.symbol:                       # restore the saved symbol context (read back, not dead data)
            self._symbol = feed.symbol
            if self._worker is not None:
                self._worker.set_symbol(feed.symbol)
        self._refresh_list()

    def _delete_feed(self) -> None:
        name = self._feed_combo.currentText()
        if name and not name.startswith("—"):
            self._store.remove(name)
            self._reload_feed_combo()
