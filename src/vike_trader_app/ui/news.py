"""News space — TradingView News-Flow-style feed: filter toolbar + (list | reader) split.

Free Tier-0 RSS/JSON sources only (see data/news/providers.py). A background _NewsWorker polls
off the UI thread and pushes batches via a signal; the aggregator merges them into the model on
the UI thread. Pure network → safe off-thread (no Parquet/Catalog reads here). Real source logos
are fetched lazily (news_logos), with a colored-initial fallback. TV-style multi-select filter
dropdowns (Market / Category / Provider) + a closeable reader (X → full-width list).
"""

from __future__ import annotations

import html
import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

from ..data.news.aggregator import apply_filter, merge
from ..data.news.classify import CATEGORIES, classify
from ..data.news.fetch import fetch_iter
from ..data.news.models import NewsFilter, NewsItem
from ..data.news.providers import PROVIDERS
from . import icons, theme
from .news_filter import MultiSelectFilter
from .news_logos import LogoStore

# Market dropdown label -> NewsItem.market code
_MARKETS = {"Crypto": "crypto", "Forex": "forex", "Stocks": "stocks", "Global": "global"}

# Deterministic provider-badge palette (fallback when no real logo is cached yet).
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
    """TradingView-style row: source logo · time · source, then the headline.

    The headline WRAPS to up to ``MAX_LINES`` lines and the row height grows to fit — a long
    title flows onto the next line instead of being clipped with an ellipsis (TV News-Flow
    behaviour). Real logo if cached (logo_store), else a colored-initial badge.
    """

    AV = 30          # source avatar size
    LEFT = 16        # row left padding (also the avatar gutter)
    GUTTER = 12      # gap from avatar to the text column
    PAD_TOP = 14
    META_H = 16      # the "time · source" line height
    GAP = 6          # meta line → headline
    PAD_BOT = 14
    MAX_LINES = 3    # headline wraps up to this many lines, then elides (rare at list widths)

    def __init__(self, parent=None, logo_store: "LogoStore | None" = None):
        super().__init__(parent)
        self._logos = logo_store

    def _badge(self, source: str, size: int) -> "QtGui.QPixmap":
        pm = self._logos.pixmap(source, size) if self._logos else None
        return pm if pm is not None else _avatar_for(source, size)

    @staticmethod
    def _title_font(base: "QtGui.QFont") -> "QtGui.QFont":
        f = QtGui.QFont(base)
        f.setPixelSize(15)                          # TV headline size
        f.setWeight(QtGui.QFont.Weight.Medium)      # ~500, lighter than bold
        return f

    def _avail_w(self, total_w: int) -> int:
        return total_w - (self.LEFT + self.AV + self.GUTTER) - self.LEFT

    def _wrapped_line_count(self, font, text: str, width: int) -> int:
        """How many lines (1..MAX_LINES) the headline needs once word-wrapped at ``width``."""
        if width <= 0 or not text:
            return 1
        layout = QtGui.QTextLayout(text, font)
        opt = QtGui.QTextOption()
        opt.setWrapMode(QtGui.QTextOption.WrapMode.WordWrap)
        layout.setTextOption(opt)
        layout.beginLayout()
        n = 0
        while n < self.MAX_LINES:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            n += 1
        layout.endLayout()
        return max(1, n)

    def sizeHint(self, opt, idx):
        w = opt.rect.width()
        it = idx.data(QtCore.Qt.UserRole)
        line_h = QtGui.QFontMetrics(self._title_font(opt.font)).lineSpacing()
        if not isinstance(it, NewsItem) or w <= 0:
            lines = 1
        else:
            lines = self._wrapped_line_count(self._title_font(opt.font), it.title, self._avail_w(w))
        return QtCore.QSize(w, self.PAD_TOP + self.META_H + self.GAP + lines * line_h + self.PAD_BOT)

    def paint(self, p, opt, idx):
        it = idx.data(QtCore.Qt.UserRole)
        if not isinstance(it, NewsItem):
            super().paint(p, opt, idx)           # empty-state placeholder → default text paint
            return
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        r = opt.rect
        # Neutral selection/hover (TV uses no colour accent on the row — just a lighter strip).
        if opt.state & QtWidgets.QStyle.State_Selected:
            p.fillRect(r, QtGui.QColor(theme.RAISE))
        elif opt.state & QtWidgets.QStyle.State_MouseOver:
            p.fillRect(r, QtGui.QColor(theme.HOVER))

        x = r.left() + self.LEFT + self.AV + self.GUTTER
        right = r.right() - self.LEFT
        # Avatar is top-anchored to the source line so multi-line rows still read cleanly.
        p.drawPixmap(r.left() + self.LEFT, r.top() + self.PAD_TOP, self._badge(it.source, self.AV))

        meta_font = QtGui.QFont(opt.font)
        meta_font.setPixelSize(12)                           # TV meta size
        meta_font.setWeight(QtGui.QFont.Weight.Normal)
        p.setFont(meta_font)
        p.setPen(QtGui.QColor(theme.TEXT3))
        meta_rect = QtCore.QRect(x, r.top() + self.PAD_TOP, right - x, self.META_H)
        p.drawText(meta_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                   f"{_ago(it.published_ms)}  ·  {it.source}")   # TV order: time · source

        p.setFont(self._title_font(opt.font))
        p.setPen(QtGui.QColor(theme.TEXT))
        self._draw_wrapped_title(p, it.title, x, r.top() + self.PAD_TOP + self.META_H + self.GAP,
                                 right - x)

        sep = QtGui.QColor(theme.BORDER)
        sep.setAlpha(140)
        p.setPen(sep)
        p.drawLine(x, r.bottom(), right, r.bottom())
        p.restore()

    def _draw_wrapped_title(self, p, text: str, x: int, top: int, width: int) -> None:
        """Paint the headline word-wrapped to ≤ MAX_LINES lines; elide only the final line."""
        fm = p.fontMetrics()
        layout = QtGui.QTextLayout(text, p.font())
        opt = QtGui.QTextOption()
        opt.setWrapMode(QtGui.QTextOption.WrapMode.WordWrap)
        layout.setTextOption(opt)
        layout.beginLayout()
        y = top
        line_h = fm.lineSpacing()
        n = 0
        while n < self.MAX_LINES:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            start, length = line.textStart(), line.textLength()
            if n == self.MAX_LINES - 1 and start + length < len(text):
                # Text overflows the last allowed line — elide the remainder onto one line.
                elided = fm.elidedText(text[start:], QtCore.Qt.ElideRight, width)
                p.drawText(QtCore.QPointF(x, y + fm.ascent()), elided)
            else:
                line.draw(p, QtCore.QPointF(x, y))
            y += line_h
            n += 1
        layout.endLayout()


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

    itemsUpdated = QtCore.Signal()   # the merged feed changed (dashboard News tile mirrors it)

    def __init__(self, providers=None, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._providers = list(providers) if providers is not None else list(PROVIDERS)
        self._items: list[NewsItem] = []
        self._symbol: str | None = None
        self._worker: _NewsWorker | None = None
        self._logos = LogoStore(self._providers, self)
        self._logos.updated.connect(self._on_logos_updated)
        self._split_sizes: list[int] | None = None

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addWidget(self._build_toolbar())

        self._split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._list = QtWidgets.QListWidget()
        self._list.setItemDelegate(_NewsRowDelegate(self._list, self._logos))   # TV-style rows
        self._list.setMouseTracking(True)                          # hover highlight
        self._list.setStyleSheet(
            f"QListWidget{{background:{theme.CHART_BG};border:none;outline:none;}}")
        # Never scroll a news feed horizontally — rows elide to the viewport width, so the
        # vertical scrollbar claiming a few px must not pop a stray horizontal bar (TV parity).
        self._list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._list.itemClicked.connect(lambda _it: self._open_reader())  # re-click reopens a closed reader
        self._split.addWidget(self._list)
        self._reader = self._build_reader()
        self._split.addWidget(self._reader)
        self._split.setStretchFactor(0, 2)        # TV proportion: list 2/3, reader 1/3
        self._split.setStretchFactor(1, 1)
        self._split.setSizes([1300, 650])
        root.addWidget(self._split, 1)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        root.addWidget(self._status)
        self._last_update = ""

    # ---- construction helpers ----
    def _build_toolbar(self) -> QtWidgets.QWidget:
        wrap = QtWidgets.QWidget()
        wrap.setObjectName("newsbar")
        wrap.setStyleSheet(
            "#newsbar QToolButton, #newsbar QPushButton, #newsbar QLineEdit {"
            f"  background:{theme.RAISE}; color:{theme.TEXT2}; border:1px solid {theme.BORDER};"
            "   border-radius:8px; padding:7px 14px; font-size:13px; }"
            "#newsbar QLineEdit {" f" color:{theme.TEXT}; " "}"
            "#newsbar QToolButton:hover, #newsbar QPushButton:hover {"
            f"  color:{theme.TEXT}; border-color:{theme.TEXT3}; }}"
            "#newsbar QToolButton:checked {" f" color:{theme.ACCENT}; border-color:{theme.ACCENT}; }}")
        bar = QtWidgets.QHBoxLayout(wrap)
        bar.setContentsMargins(2, 2, 2, 4)
        bar.setSpacing(8)

        # TV-style multi-select filter dropdowns (empty selection == no constraint).
        # TV left-aligns the filter pills in a row; search is NOT inline (it's the global search),
        # so we keep our search but push it (with refresh) to the right, filters on the left.
        self._market = MultiSelectFilter("Market", list(_MARKETS.keys()))
        self._market.selectionChanged.connect(self._refresh_list)
        self._category = MultiSelectFilter("Category", CATEGORIES)
        self._category.selectionChanged.connect(self._refresh_list)
        self._provider = MultiSelectFilter("Provider", sorted({p.name for p in self._providers}))
        self._provider.selectionChanged.connect(self._refresh_list)

        self._follow = QtWidgets.QToolButton()
        self._follow.setText("⌖ Follow chart")
        self._follow.setCheckable(True)
        self._follow.setChecked(True)                 # default ON (chart-centric app)
        self._follow.toggled.connect(self._on_follow_toggled)

        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Search headlines…")
        self._search.setMaximumWidth(280)
        self._search.textChanged.connect(lambda _t: self._refresh_list())

        self._refresh_btn = QtWidgets.QPushButton("↻")
        self._refresh_btn.clicked.connect(lambda: self._worker and self._worker.refresh_now())

        for w in (self._market, self._category, self._provider, self._follow):
            bar.addWidget(w)                          # filters left-aligned (TV placement)
        bar.addStretch(1)
        bar.addWidget(self._search)                   # search + refresh on the right
        bar.addWidget(self._refresh_btn)
        return wrap

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
        self._close_btn = QtWidgets.QToolButton()
        self._close_btn.setText("✕")
        self._close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._close_btn.setStyleSheet(
            f"QToolButton{{color:{theme.TEXT3};border:none;font-size:16px;padding:2px 6px;}}"
            f"QToolButton:hover{{color:{theme.TEXT};}}")
        self._close_btn.clicked.connect(self.close_reader)
        head.addWidget(self._reader_av)
        head.addWidget(self._source_lbl)
        head.addStretch(1)
        head.addWidget(self._close_btn)
        v.addLayout(head)
        self._title = QtWidgets.QLabel("Select a headline")
        self._title.setWordWrap(True)
        self._title.setStyleSheet(
            f"color:{theme.TEXT};font-size:26px;font-weight:700;line-height:128%;")
        self._meta = QtWidgets.QLabel("")
        self._meta.setStyleSheet(f"color:{theme.TEXT2};font-size:13px;")
        self._body = QtWidgets.QTextBrowser()
        self._body.setOpenExternalLinks(False)
        self._body.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)  # text wraps; no sideways scroll
        self._body.setStyleSheet("QTextBrowser{border:none;background:transparent;}")
        self._chips = QtWidgets.QLabel("")          # TV-style topic tags, at the bottom
        self._chips.setTextFormat(QtCore.Qt.RichText)
        self._chips.setWordWrap(True)
        self._chips.setVisible(False)
        self._open_btn = QtWidgets.QPushButton("↗ Open original")
        self._open_btn.clicked.connect(self._open_original)
        self._open_btn.setEnabled(False)
        for x in (self._title, self._meta, self._body, self._chips, self._open_btn):
            v.addWidget(x)
        # Let the body grow with its content (and scroll inside itself for long articles) while
        # a trailing stretch — not the body — absorbs the leftover height. This keeps the chips +
        # "Open original" button hugging the body instead of being flung to the very bottom of the
        # pane with a large dead gap above them.
        self._body.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Maximum)
        self._body.setMinimumHeight(120)
        v.addStretch(1)
        return w

    def _chip_html(self, it: NewsItem) -> str:
        ordered = [classify(it), *it.tags, *(it.market.capitalize() if it.market else "",), *it.symbols]
        seen: set[str] = set()
        chips: list[str] = []
        for c in ordered:
            c = (c or "").strip()
            if c and c.lower() not in seen:
                seen.add(c.lower())
                chips.append(c)
        cell = (f"<span style='background:{theme.RAISE};color:{theme.TEXT};"
                f"padding:3px 10px;margin-right:6px;font-size:12px;'>&nbsp;{{t}}&nbsp;</span>")
        return "".join(cell.format(t=html.escape(c)) for c in chips[:6])

    # ---- feed lifecycle ----
    def start_feed(self, symbol: str | None = None) -> None:
        """Lazily start the background poller (called when the News space is first opened)."""
        if symbol is not None:
            self._symbol = symbol
        self._logos.prefetch_async()           # pull real source logos in the background (one shot)
        if self._worker is not None:
            return
        self._worker = _NewsWorker(self._providers, self._symbol, follow=self._follow.isChecked())
        self._worker.itemsReceived.connect(self.on_items_received)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def stop_feed(self) -> None:
        self._logos.stop()
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

    # ---- reader open/close ----
    def close_reader(self) -> None:
        """Hide the reader → the list goes full-width (TV's X button)."""
        if not self._reader.isHidden():                # isHidden() reflects the flag even when unshown
            self._split_sizes = self._split.sizes()
            self._reader.setVisible(False)

    def _open_reader(self) -> None:
        if self._reader.isHidden():
            self._reader.setVisible(True)
            if self._split_sizes:
                self._split.setSizes(self._split_sizes)

    # ---- slots ----
    def on_items_received(self, items) -> None:
        try:
            self._items = merge(self._items, list(items))
            self._last_update = time.strftime("%H:%M:%S")
            self._refresh_list()
            self.itemsUpdated.emit()
        except RuntimeError:
            return   # the tab was torn down (tool closed) while a worker batch was in flight

    def _on_failed(self, message: str) -> None:
        self._status.setText(f"Feed error: {message}")     # status line, never a modal

    def _on_logos_updated(self) -> None:
        try:
            self._list.viewport().update()                 # repaint rows with freshly-cached logos
            it = self._current_item()
            if it is not None:
                self._reader_av.setPixmap(self._badge(it.source, 30))
        except RuntimeError:
            return   # the tab was torn down while the logo fetcher was still running

    def _on_follow_toggled(self, on: bool) -> None:
        if self._worker is not None:
            self._worker.set_follow(on)
        self._refresh_list()

    def _badge(self, source: str, size: int) -> "QtGui.QPixmap":
        pm = self._logos.pixmap(source, size)
        return pm if pm is not None else _avatar_for(source, size)

    def _on_row_changed(self, _row: int) -> None:
        it = self._current_item()
        if it is None:
            return
        self._open_reader()                                # re-open if the user had closed it
        self._reader_av.setPixmap(self._badge(it.source, 30))
        self._source_lbl.setText(it.source)
        self._title.setText(it.title)
        when = time.strftime("%b %d, %Y · %H:%M", time.localtime(it.published_ms / 1000)) if it.published_ms else ""
        self._meta.setText(f"{when}  ·  {_ago(it.published_ms)}" if when else "")
        summary = html.escape(it.summary or "(no summary — open the original)").replace("\n", "<br>")
        self._body.setHtml(
            f"<div style='color:{theme.TEXT2};font-size:16px;line-height:170%;'>{summary}</div>")
        self._chips.setText(self._chip_html(it))
        self._chips.setVisible(bool(self._chips.text()))
        self._open_btn.setEnabled(bool(it.url))

    def _open_original(self) -> None:
        it = self._current_item()
        if it and it.url:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(it.url))

    # ---- filter + list ----
    def _current_filter(self) -> NewsFilter:
        markets = frozenset(_MARKETS[label] for label in self._market.selected())
        sym = self._symbol if self._follow.isChecked() else None
        return NewsFilter(markets=markets, providers=frozenset(self._provider.selected()),
                          categories=frozenset(self._category.selected()),
                          symbol=sym, query=self._search.text().strip())

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
        # Default to the latest article (list is newest-first) so opening the News space or
        # switching a filter auto-opens the most recent headline — but only when nothing is
        # selected yet, so a 60s poll refresh never yanks the reader off what you're reading.
        if filtered and self._list.currentRow() < 0:
            self._list.setCurrentRow(0)   # fires _on_row_changed(0) -> opens newest in the reader
        self._update_status(len(filtered))

    def _empty_hint(self) -> str:
        """Explain *why* the list is empty (e.g. Follow-chart scoping) — not just a blank pane."""
        f = self._current_filter()
        markets = ", ".join(sorted(self._market.selected()))
        scope = f"{markets} " if markets else ""
        if f.symbol:
            return (f"No {scope}headlines mention {f.symbol}. "
                    f"Turn off “⌖ Follow chart” to see all {scope}news.")
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
