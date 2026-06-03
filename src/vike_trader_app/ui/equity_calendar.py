"""Equity calendars (Earnings / Dividends / IPO) + the Calendar space sub-tab bar.

A generic, date-grouped table fed by an injectable `fetch(from, to)` callable (the
equity providers in data/calendar/equity.py). Loads off the UI thread with the same
safe lifecycle as the Economic tab: header updates immediately on navigation, the
latest navigation wins, and fetch threads are waited on at app quit (no shutdown crash).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import dropdowns, icons, theme

_DAY_MS = 24 * 3600 * 1000


def _nav_button(direction: str) -> QtWidgets.QPushButton:
    """A week-nav button (‹ / ›) using the unified thin chevron icon at the shared arrow size,
    so left/right read identically to the up/down chevrons elsewhere (the TradingView look)."""
    b = QtWidgets.QPushButton()
    b.setIcon(icons.chevron_icon(direction, theme.TEXT2))
    b.setIconSize(QtCore.QSize(icons.ARROW_PX, icons.ARROW_PX))
    b.setCursor(QtCore.Qt.PointingHandCursor)
    return b
_HOUR_LABEL = {"bmo": "Pre-mkt", "amc": "After-hrs", "dmh": "Mid-day"}

# TV day-card categories (row order + label).
_CARD_CATS = [("economic", "Economic"), ("earnings", "Earnings"),
              ("dividends", "Dividends"), ("ipo", "IPO")]


class _DayCard(QtWidgets.QFrame):
    """One day's card (TV-style): weekday+day title, then one row per NON-ZERO category."""

    clicked = QtCore.Signal(int)   # day index 0..6

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self._index = index
        self.setProperty("class", "Panel")
        # One flat fill per card (TV-style): the global .Panel bg + `.Panel *{transparent}` so the
        # per-row child widgets don't paint the window BG and band the card into two tones.
        self._base_qss = (f".Panel{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
                          f"border-radius:{theme.RADIUS_LG}px;}} .Panel *{{background:transparent;}}")
        self.setStyleSheet(self._base_qss)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(3)
        self._title = QtWidgets.QLabel("")
        self._title.setStyleSheet(f"color:{theme.TEXT};font-weight:600;font-size:13px;border:none;")
        v.addWidget(self._title)
        self._rows: dict = {}
        for key, label in _CARD_CATS:
            row = QtWidgets.QWidget()
            rl = QtWidgets.QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(6)
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet(f"color:{theme.TEXT2};font-size:12px;border:none;")
            num = QtWidgets.QLabel("")
            num.setStyleSheet(f"color:{theme.TEXT};font-size:12px;font-weight:600;border:none;")
            num.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            rl.addWidget(lbl)
            rl.addStretch(1)
            rl.addWidget(num)
            v.addWidget(row)
            self._rows[key] = (row, num)
        v.addStretch(1)

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_counts(self, counts: dict) -> None:
        """int -> show row + number; 0 -> hide row; None -> '…' (still loading)."""
        for key, _label in _CARD_CATS:
            row, num = self._rows[key]
            n = counts.get(key)
            if n is None:
                row.setVisible(True)
                num.setText("…")
            elif n > 0:
                row.setVisible(True)
                num.setText(str(n))
            else:
                row.setVisible(False)

    def set_selected(self, on: bool) -> None:
        if on:   # single flat lighter fill + accent border (like TV's selected day), no header strip
            self.setStyleSheet(
                f".Panel{{background:{theme.HOVER};border:1px solid {theme.ACCENT};"
                f"border-radius:{theme.RADIUS_LG}px;}} .Panel *{{background:transparent;}}")
        else:
            self.setStyleSheet(self._base_qss)
        self._title.setStyleSheet(
            f"color:{theme.ACCENT if on else theme.TEXT};font-weight:600;font-size:13px;"
            "border:none;background:transparent;")

    def mousePressEvent(self, e):  # noqa: N802 - Qt override
        self.clicked.emit(self._index)
        super().mousePressEvent(e)


def _fmt(v, suffix="") -> str:
    if v is None:
        return "—"
    s = str(int(v)) if float(v).is_integer() else f"{v:.2f}"
    return f"{s}{suffix}"


def _fmt_big(v) -> str:
    """Money magnitude: 9.4e10 -> '94.0B'."""
    if v is None:
        return "—"
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:.1f}{suf}"
    return f"{v:.0f}"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    return f"{'+' if v > 0 else ''}{v:.1f}%"


def _fmt_cap(millions) -> str:
    """Market cap given in USD millions -> '24.2B' / '930M' / '1.4T'."""
    if not millions:
        return "—"
    if millions >= 1_000_000:
        return f"{millions / 1e6:.1f}T"
    if millions >= 1000:
        return f"{millions / 1000:.1f}B"
    return f"{millions:.0f}M"


def _surprise_color(v):
    if v is None:
        return None
    return theme.UP if v > 0 else (theme.DOWN if v < 0 else None)


# ---- per-type config: dict of EquityCalendarTab kwargs --------------------------------
def _earnings_cfg():
    # match TradingView's Earnings layout: Symbol · Company · EPS est/act · Surprise · Mkt cap
    def row(e):
        return [_HOUR_LABEL.get(e.hour, "—"), e.symbol, e.name or "—",
                _fmt(e.eps_estimate), _fmt(e.eps_actual), _fmt_pct(e.surprise), _fmt_cap(e.market_cap)]
    return {
        "columns": ["Time", "Symbol", "Company", "EPS est.", "EPS act.", "Surprise", "Mkt cap"],
        "stretch_col": 2,            # Company is the wide free-text column (like Economic's Event)
        "row_fn": row, "date_of": lambda e: e.date,
        "sort_key": lambda e: -((e.market_cap or 0) + (0 if e.market_cap else (e.rev_estimate or 0) / 1e6)),
        "covered_of": lambda e: e.eps_estimate is not None,   # has analyst coverage
        "color_of": lambda e: (5, _surprise_color(e.surprise)),
    }


def _dividends_cfg():
    # Company column (enriched via fetch_dividends_enriched) so Dividends has the SAME
    # Symbol · Company(stretch) · … shape as Earnings/IPO — consistent Symbol width across tabs,
    # and Company is the wide free-text column that fills the row (no lopsided empty ticker column).
    def row(d):
        return [d.symbol, d.name or "—", d.ex_date, d.pay_date or "—", _fmt(d.amount, " $"),
                _fmt(d.yield_pct, "%") if d.yield_pct is not None else "—", d.frequency or "—"]
    return {"columns": ["Symbol", "Company", "Ex-date", "Pay date", "Amount", "Yield", "Freq"],
            "stretch_col": 1,        # Company is the wide free-text column (like Earnings/IPO)
            "row_fn": row, "date_of": lambda d: d.ex_date}


def _ipo_cfg():
    def row(i):
        return [i.symbol or "—", i.name, i.exchange, i.price or "—", _fmt_big(i.shares), i.status]
    return {"columns": ["Symbol", "Company", "Exchange", "Price", "Shares", "Status"],
            "stretch_col": 1,        # Company is the wide free-text column (like Economic's Event)
            "row_fn": row, "date_of": lambda i: i.date}


class _EquityFetchWorker(QtCore.QThread):
    ready = QtCore.Signal(object)   # list of events

    def __init__(self, fetch, frm: str, to: str):
        super().__init__()
        self._fetch, self._frm, self._to = fetch, frm, to

    def run(self):
        try:
            self.ready.emit(self._fetch(self._frm, self._to))
        except Exception:  # noqa: BLE001
            self.ready.emit([])


class EquityCalendarTab(QtWidgets.QWidget):
    """One equity calendar (earnings/dividends/ipo). `fetch(from_str, to_str)` returns
    a list of typed events; `row_fn` maps an event to cell texts; `date_of` returns the
    event's grouping date (YYYY-MM-DD)."""

    eventsChanged = QtCore.Signal()   # emitted after a week's events land (for day-card counts)

    def __init__(self, *, fetch, columns, row_fn, date_of, stretch_col=1,
                 sort_key=None, covered_of=None, color_of=None, parent=None):
        super().__init__(parent)
        self._fetch, self._row_fn, self._date_of = fetch, row_fn, date_of
        self._sort_key = sort_key          # within-date ordering (e.g. biggest first)
        self._covered_of = covered_of      # predicate -> enables a "Covered only" toggle
        self._color_of = color_of          # event -> (col, hex|None) cell color (e.g. surprise)
        self._covered_only = covered_of is not None   # default ON when supported
        self._events: list = []
        self._filter = ""
        self._now = lambda: int(time.time() * 1000)
        self._week_start = _week_start(self._now())
        self._loaded = False
        self._loading_week: int | None = None
        self._workers: set = set()
        self._worker = None

        root = QtWidgets.QVBoxLayout(self)
        root.addWidget(self._build_toolbar())
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        root.addWidget(self._status)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(len(columns))
        self._tree.setHeaderLabels(columns)
        # Match the Economic tree exactly (TV-polished): no decoration/indent, no alt rows, and a
        # single wide Stretch column (its free-text column, like Economic's Event) with the rest
        # left at Qt's default Interactive sizing. Header styling, row padding and the mono cell
        # font all come from the shared QSS (QHeaderView::section / QTreeWidget::item), so the four
        # calendars read identically. (Was: hard-coded Stretch on col 1 — wrong column for
        # Dividends/IPO, which is why those tables looked unaligned vs. Economic.)
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        self._tree.setAlternatingRowColors(False)
        # Content-size every data column so they're snug AND consistent across the three equity
        # tabs (the Symbol column is then the same width everywhere), then let the one wide
        # free-text column (Company / Event) absorb the remaining width.
        hdr = self._tree.header()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(stretch_col, QtWidgets.QHeaderView.Stretch)
        root.addWidget(self._tree, 1)

        _app = QtWidgets.QApplication.instance()
        if _app is not None:
            _app.aboutToQuit.connect(self._stop_workers)

    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        btn_today = QtWidgets.QPushButton("Today")
        btn_today.clicked.connect(self.go_today)
        prev = _nav_button("left")
        prev.clicked.connect(lambda: self._nav(-_DAY_MS * 7))
        nxt = _nav_button("right")
        nxt.clicked.connect(lambda: self._nav(_DAY_MS * 7))
        self._lbl_range = QtWidgets.QLabel("")
        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Filter symbol…")
        self._search.setMaximumWidth(160)
        self._search.textChanged.connect(self._on_filter)
        self._nav_widgets = [btn_today, prev, nxt, self._lbl_range]
        for w in self._nav_widgets:
            h.addWidget(w)
        h.addStretch(1)
        if self._covered_of is not None:
            self._chk_covered = QtWidgets.QCheckBox("Covered only")
            self._chk_covered.setChecked(self._covered_only)
            self._chk_covered.toggled.connect(self._on_covered_toggled)
            h.addWidget(self._chk_covered)
        h.addWidget(self._search)
        return bar

    def _on_covered_toggled(self, on: bool) -> None:
        self._covered_only = on
        self._rebuild()

    # ---- navigation ----
    def current_week_start(self) -> int:
        return self._week_start

    def go_today(self) -> None:
        self._week_start = _week_start(self._now())
        self.refresh_async()

    def _nav(self, delta_ms: int) -> None:
        self._week_start += delta_ms
        self.refresh_async()

    def showEvent(self, event):  # noqa: N802 - load when the sub-tab is first shown
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh_async()

    # ---- async load (same safe pattern as the Economic tab) ----
    def refresh_async(self) -> None:
        self._refresh_range_label()
        self._tree.clear()
        self._status.setText("Loading…")
        if self._loading_week is None:
            self._start_worker()

    def _start_worker(self) -> None:
        self._loading_week = self._week_start
        frm, to = _week_dates(self._week_start)
        w = _EquityFetchWorker(self._fetch, frm, to)
        w.ready.connect(self._on_ready)
        w.finished.connect(self._on_finished)
        self._worker = w
        self._workers.add(w)
        w.start()

    def _on_ready(self, events) -> None:
        if self._loading_week == self._week_start:
            self._events = events
            self._rebuild()
            self.eventsChanged.emit()

    def _on_finished(self) -> None:
        self._workers.discard(self.sender())
        done = self._loading_week
        self._loading_week = None
        if done != self._week_start:
            self._start_worker()

    def _stop_workers(self) -> None:
        # Enrichment fetches can be slow (rate-limited); give them a moment, then force-stop
        # so the QThread is never destroyed mid-run (which crashes the process at exit).
        for w in list(self._workers):
            try:
                if w.isRunning() and not w.wait(2000):
                    w.terminate()
                    w.wait(1000)
            except RuntimeError:
                pass

    # ---- filter ----
    def _on_filter(self, text: str) -> None:
        self._filter = text.strip().upper()
        self._rebuild()

    def visible_event_count(self) -> int:
        n = 0
        for i in range(self._tree.topLevelItemCount()):
            n += self._tree.topLevelItem(i).childCount()
        return n

    # ---- render ----
    def load(self) -> None:
        """Synchronous load (used by tests)."""
        frm, to = _week_dates(self._week_start)
        self._events = self._fetch(frm, to)
        self._rebuild()

    def set_now_ms(self, ms: int) -> None:
        self._now = lambda: ms
        self._week_start = _week_start(ms)

    def _rebuild(self) -> None:
        self._tree.clear()
        rows = [e for e in self._events
                if (not self._filter or self._filter in str(getattr(e, "symbol", "")).upper())
                and not (self._covered_only and self._covered_of and not self._covered_of(e))]
        key = (lambda e: (self._date_of(e), self._sort_key(e))) if self._sort_key else self._date_of
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for ev in sorted(rows, key=key):
            day = self._date_of(ev)
            parent = groups.get(day)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem([_pretty_date(day)])
                parent.setFirstColumnSpanned(True)
                f = parent.font(0)
                f.setBold(True)
                parent.setFont(0, f)
                self._tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                groups[day] = parent
            item = QtWidgets.QTreeWidgetItem(self._row_fn(ev))
            if self._color_of:
                col, color = self._color_of(ev)
                if color:
                    item.setForeground(col, QtGui.QColor(color))
            parent.addChild(item)
        self._status.setText("" if rows else "No events for this week.")

    def _refresh_range_label(self) -> None:
        a = datetime.fromtimestamp(self._week_start / 1000, tz=timezone.utc)
        b = datetime.fromtimestamp((self._week_start + 6 * _DAY_MS) / 1000, tz=timezone.utc)
        self._lbl_range.setText(f"{a.strftime('%b')} {a.day} — {b.strftime('%b')} {b.day}, {b.year}")


class CalendarSpace(QtWidgets.QWidget):
    """The Calendar space: a sub-tab pill bar (Economic / Earnings / Dividends / IPO)
    over a stacked set of calendars. Economic is the existing macro calendar; the others
    are equity calendars from Finnhub/FMP."""

    def __init__(self, economic_tab=None, parent=None):
        super().__init__(parent)
        from .economic_calendar import EconomicCalendarTab
        from ..data.calendar.equity import Ipo, fetch_dividends_enriched, fetch_earnings_enriched

        self.economic = economic_tab or EconomicCalendarTab()
        self.earnings = EquityCalendarTab(fetch=fetch_earnings_enriched, **_earnings_cfg())
        self.dividends = EquityCalendarTab(fetch=fetch_dividends_enriched, **_dividends_cfg())
        self.ipo = EquityCalendarTab(fetch=Ipo().fetch, **_ipo_cfg())

        self._stack = QtWidgets.QStackedWidget()
        self._pages = [("Economic", self.economic), ("Earnings", self.earnings),
                       ("Dividends", self.dividends), ("IPO", self.ipo)]
        pills = QtWidgets.QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        pills.setSpacing(4)
        pill_qss = (
            f"QPushButton{{background:transparent;border:none;color:{theme.TEXT3};"
            f"padding:6px 16px;border-radius:9px;font-size:13px;font-weight:600;}}"
            f"QPushButton:hover{{color:{theme.TEXT2};background:{theme.HOVER};}}"
            f"QPushButton:checked{{background:{theme.HOVER};color:{theme.TEXT};}}")
        self._pills: list[QtWidgets.QPushButton] = []
        for i, (name, page) in enumerate(self._pages):
            self._stack.addWidget(page)
            b = QtWidgets.QPushButton(name)
            b.setCheckable(True)
            b.setStyleSheet(pill_qss)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _c, idx=i: self.set_page(idx))
            pills.addWidget(b)
            self._pills.append(b)
        pills.addStretch(1)
        pills.addWidget(self._build_category_dropdown())   # TV "All categories", right side
        self.economic._cmb_cat.setVisible(False)           # de-dupe: this dropdown replaces it

        # TV-style day-card strip (Economic/Earnings/Dividends/IPO counts), ABOVE the pills.
        self._selected_day = -1
        self._primed = False
        self._day_cards: list[_DayCard] = []
        strip = QtWidgets.QWidget()
        sl = QtWidgets.QHBoxLayout(strip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        for i in range(7):
            card = _DayCard(i)
            card.clicked.connect(self._on_day_clicked)
            self._day_cards.append(card)
            sl.addWidget(card)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(10)
        root.addWidget(self._build_topnav())   # TV: date-nav row ABOVE the day-cards
        root.addWidget(strip)
        bar = QtWidgets.QWidget()
        bar.setLayout(pills)
        root.addWidget(bar)
        root.addWidget(self._stack, 1)
        # the shared top date-nav drives the week for every page (all tabs follow economic),
        # so hide each sub-tab's own date-nav + economic's timezone selector to avoid dupes.
        for tab in (self.economic, self.earnings, self.dividends, self.ipo):
            for w in getattr(tab, "_nav_widgets", []):
                w.setVisible(False)
        self.economic._cmb_tz.setVisible(False)
        self.economic._toolbar.setVisible(False)   # High-only/Countries moved to the top nav (TV layout)
        self.set_page(0)
        self._sync_range()

        # recount day-cards whenever any source's data changes
        self.economic.eventsChanged.connect(self._on_economic_changed)
        for tab in (self.earnings, self.dividends, self.ipo):
            tab.eventsChanged.connect(self.refresh_day_counts)

    def set_page(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        for i, b in enumerate(self._pills):
            b.setChecked(i == idx)
        if hasattr(self, "_cat_btn"):
            self._cat_btn.setVisible(idx == 0)   # category filter only applies to Economic
        if hasattr(self, "_top_high"):           # economic-only date-nav filters
            self._top_high.setVisible(idx == 0)
            self._top_countries.setVisible(idx == 0)

    # ---- TV "All categories" dropdown (filters the Economic page) — shared single-select pill ----
    def _build_category_dropdown(self) -> QtWidgets.QWidget:
        opts = [("All", "All categories"), ("rates", "Rates"), ("inflation", "Inflation"),
                ("employment", "Employment"), ("gdp", "GDP"), ("trade", "Trade"),
                ("housing", "Housing"), ("other", "Other")]
        pill = dropdowns.FilterPill("All categories", opts, mode="single")
        pill.set_current("All")
        pill.selectionChanged.connect(self._on_category_changed)
        self._cat_btn = pill
        return pill

    def _on_category_changed(self) -> None:
        self.economic.set_category(self._cat_btn.current() or "All")
        self.refresh_day_counts()

    # ---- TV day-card strip (aggregates all four calendars) ----
    def showEvent(self, event):  # noqa: N802 - prime equity fetches when the space first opens
        super().showEvent(event)
        if not self._primed:
            self._primed = True
            self._prime_equity()
            self.refresh_day_counts()

    def _prime_equity(self) -> None:
        """Kick the equity fetches so the cards can count all categories without opening pills."""
        origin = self.economic.current_week_start()
        for tab in (self.earnings, self.dividends, self.ipo):
            tab._week_start = origin
            tab._loaded = True
            tab.refresh_async()

    def _on_economic_changed(self) -> None:
        origin = self.economic.current_week_start()
        for tab in (self.earnings, self.dividends, self.ipo):
            if tab._week_start != origin:          # economic navigated -> follow so counts align
                tab._week_start = origin
                if tab._loaded:
                    tab.refresh_async()
        self.refresh_day_counts()
        self._sync_range()

    # ---- shared top date-nav (TV-style, drives the week for every page) ----
    def _build_topnav(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        bar.setStyleSheet(
            f"QPushButton{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
            f"border-radius:{theme.RADIUS_MD}px;padding:6px 13px;color:{theme.TEXT2};font-size:13px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};border-color:{theme.TEXT3};}}"
            f"QComboBox{{background:{theme.SURFACE};border:1px solid {theme.BORDER};"
            f"border-radius:{theme.RADIUS_MD}px;padding:6px 10px;color:{theme.TEXT2};font-size:13px;}}"
            f"QCheckBox{{color:{theme.TEXT2};font-size:13px;spacing:6px;padding:6px 4px;}}"
            f"QCheckBox:hover{{color:{theme.TEXT};}}")
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        btn_today = QtWidgets.QPushButton("Today")
        btn_today.setCursor(QtCore.Qt.PointingHandCursor)
        btn_today.clicked.connect(lambda: (self.economic.go_today(), self._sync_range()))
        prev = _nav_button("left")
        prev.clicked.connect(lambda: (self.economic.go_prev_week(), self._sync_range()))
        nxt = _nav_button("right")
        nxt.clicked.connect(lambda: (self.economic.go_next_week(), self._sync_range()))
        self._range_lbl = QtWidgets.QLabel("")
        self._range_lbl.setStyleSheet(
            f"color:{theme.TEXT};font-size:16px;font-weight:600;border:none;padding-left:6px;")
        for w in (btn_today, prev, nxt):
            h.addWidget(w)
        h.addWidget(self._range_lbl)
        h.addStretch(1)
        # Economic-only filters live here (right side of the date-nav, like TV's importance/G20),
        # not in a separate row under the pills. Shown only on the Economic page (see set_page).
        self._top_high = QtWidgets.QCheckBox("High only")
        self._top_high.setCursor(QtCore.Qt.PointingHandCursor)
        self._top_high.toggled.connect(self.economic._chk_high.setChecked)   # drives set_high_only
        self._top_countries = self._build_country_pill()
        h.addWidget(self._top_high)
        h.addWidget(self._top_countries)
        self._tz = QtWidgets.QComboBox()
        self._tz.setCursor(QtCore.Qt.PointingHandCursor)
        self._tz.addItems([self.economic._cmb_tz.itemText(i)
                           for i in range(self.economic._cmb_tz.count())])
        self._tz.setCurrentIndex(self.economic._cmb_tz.currentIndex())
        self._tz.currentIndexChanged.connect(self.economic._cmb_tz.setCurrentIndex)
        # Size to the widest label (+arrow +QSS padding) so "UTC+8" etc. don't elide to "U...8".
        self._tz.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self._tz.setMinimumWidth(104)
        h.addWidget(self._tz)
        return bar

    def _build_country_pill(self) -> QtWidgets.QWidget:
        """Shared multi-select pill of currencies (flag-iconed, searchable) — replaces the modal.

        Empty selection == all countries (matches economic.set_countries(None))."""
        from ..data.calendar.taxonomy import (
            ALL_CURRENCIES, COUNTRY_REGIONS, TOP20_ECONOMIES, currency_country)
        from .economic_calendar import country_chip_pixmap
        opts: list[tuple[str, str]] = []
        icons: dict[str, QtGui.QIcon] = {}
        for currencies in COUNTRY_REGIONS.values():
            for cur in currencies:
                name, iso = currency_country(cur)
                opts.append((cur, name))
                icons[cur] = QtGui.QIcon(country_chip_pixmap(iso))
        # quick-picks: Entire world / Top 20 (header slot of the popover)
        qp = QtWidgets.QWidget()
        qpl = QtWidgets.QHBoxLayout(qp)
        qpl.setContentsMargins(0, 0, 0, 0)
        qpl.setSpacing(6)
        b_world = QtWidgets.QPushButton("Entire world")
        b_top = QtWidgets.QPushButton("Top 20")
        for b in (b_world, b_top):
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.setStyleSheet(
                f"QPushButton{{background:{theme.BG};color:{theme.BLUE};border:none;"
                f"border-radius:{theme.RADIUS_SM}px;padding:4px 9px;font-size:11px;}}")
            qpl.addWidget(b)
        qpl.addStretch(1)
        pill = dropdowns.FilterPill("Countries", opts, mode="multi",
                                    row_icons=icons, header_widgets=[qp])
        b_world.clicked.connect(lambda: pill.set_selected(set(ALL_CURRENCIES)))
        b_top.clicked.connect(lambda: pill.set_selected(set(TOP20_ECONOMIES)))
        pill.selectionChanged.connect(self._on_countries_changed)
        return pill

    def _on_countries_changed(self) -> None:
        self.economic.set_countries(self._top_countries.selected() or None)
        self.refresh_day_counts()

    def _sync_range(self) -> None:
        if hasattr(self, "_range_lbl"):
            self._range_lbl.setText(self.economic._lbl_range.text())

    def refresh_day_counts(self) -> None:
        origin = self.economic.current_week_start()
        for i, card in enumerate(self._day_cards):
            # Title + bucket by the Economic tab's DISPLAY timezone, so the cards line up with
            # the day-group headers in the Economic tree (which groups its rows by local date).
            # A macro print at 23:30Z under UTC+8 belongs to the next local day in BOTH places.
            day = self.economic._local(origin + i * _DAY_MS).date()
            card.set_title(f"{day.strftime('%a')} {day.day}")
            counts: dict = {"economic": sum(1 for e in self.economic._events
                                            if self.economic._local(e.ts_utc).date() == day)}
            for key, tab in (("earnings", self.earnings), ("dividends", self.dividends),
                             ("ipo", self.ipo)):
                if tab._loading_week is not None and not tab._events:
                    counts[key] = None             # still loading, nothing yet -> '…'
                else:
                    counts[key] = sum(1 for e in tab._events if tab._date_of(e) == day.isoformat())
            card.set_counts(counts)
            card.set_selected(i == self._selected_day)

    def _on_day_clicked(self, index: int) -> None:
        self._selected_day = index
        for i, card in enumerate(self._day_cards):
            card.set_selected(i == index)
        # Navigate: show the Economic table and scroll it to the clicked day's group.
        self.set_page(0)
        eco = self.economic
        header = eco._date_header(eco.current_week_start() + index * _DAY_MS)
        tree = eco._tree
        for r in range(tree.topLevelItemCount()):
            top = tree.topLevelItem(r)
            if top.text(0) == header:
                tree.scrollToItem(top, QtWidgets.QAbstractItemView.PositionAtTop)
                tree.setCurrentItem(top)
                break


# ---- week helpers (UTC week, like the Economic calendar's nav unit) ----
def _week_start(ts_ms: int) -> int:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    monday = (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp() * 1000)


def _week_dates(week_start_ms: int) -> tuple[str, str]:
    a = datetime.fromtimestamp(week_start_ms / 1000, tz=timezone.utc)
    b = datetime.fromtimestamp((week_start_ms + 6 * _DAY_MS) / 1000, tz=timezone.utc)
    return a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")


def _pretty_date(ymd: str) -> str:
    try:
        dt = datetime.strptime(ymd, "%Y-%m-%d")
        return f"{dt.strftime('%A, %B')} {dt.day}"
    except ValueError:
        return ymd
