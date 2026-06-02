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

from . import theme

_DAY_MS = 24 * 3600 * 1000
_HOUR_LABEL = {"bmo": "Pre-mkt", "amc": "After-hrs", "dmh": "Mid-day"}


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
        "row_fn": row, "date_of": lambda e: e.date,
        "sort_key": lambda e: -((e.market_cap or 0) + (0 if e.market_cap else (e.rev_estimate or 0) / 1e6)),
        "covered_of": lambda e: e.eps_estimate is not None,   # has analyst coverage
        "color_of": lambda e: (5, _surprise_color(e.surprise)),
    }


def _dividends_cfg():
    def row(d):
        return [d.symbol, d.ex_date, d.pay_date or "—", _fmt(d.amount, " $"),
                _fmt(d.yield_pct, "%") if d.yield_pct is not None else "—", d.frequency or "—"]
    return {"columns": ["Symbol", "Ex-date", "Pay date", "Amount", "Yield", "Freq"],
            "row_fn": row, "date_of": lambda d: d.ex_date}


def _ipo_cfg():
    def row(i):
        return [i.symbol or "—", i.name, i.exchange, i.price or "—", _fmt_big(i.shares), i.status]
    return {"columns": ["Symbol", "Company", "Exchange", "Price", "Shares", "Status"],
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

    def __init__(self, *, fetch, columns, row_fn, date_of,
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
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        self._tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
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
        prev = QtWidgets.QPushButton("‹")
        prev.clicked.connect(lambda: self._nav(-_DAY_MS * 7))
        nxt = QtWidgets.QPushButton("›")
        nxt.clicked.connect(lambda: self._nav(_DAY_MS * 7))
        self._lbl_range = QtWidgets.QLabel("")
        self._search = QtWidgets.QLineEdit()
        self._search.setPlaceholderText("Filter symbol…")
        self._search.setMaximumWidth(160)
        self._search.textChanged.connect(self._on_filter)
        for w in (btn_today, prev, nxt, self._lbl_range):
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
        from ..data.calendar.equity import FinnhubIpo, FmpDividends, fetch_earnings_enriched

        self.economic = economic_tab or EconomicCalendarTab()
        self.earnings = EquityCalendarTab(fetch=fetch_earnings_enriched, **_earnings_cfg())
        self.dividends = EquityCalendarTab(fetch=FmpDividends().fetch, **_dividends_cfg())
        self.ipo = EquityCalendarTab(fetch=FinnhubIpo().fetch, **_ipo_cfg())

        self._stack = QtWidgets.QStackedWidget()
        self._pages = [("Economic", self.economic), ("Earnings", self.earnings),
                       ("Dividends", self.dividends), ("IPO", self.ipo)]
        pills = QtWidgets.QHBoxLayout()
        pills.setContentsMargins(0, 0, 0, 0)
        self._pills: list[QtWidgets.QPushButton] = []
        for i, (name, page) in enumerate(self._pages):
            self._stack.addWidget(page)
            b = QtWidgets.QPushButton(name)
            b.setCheckable(True)
            b.clicked.connect(lambda _c, idx=i: self.set_page(idx))
            pills.addWidget(b)
            self._pills.append(b)
        pills.addStretch(1)

        root = QtWidgets.QVBoxLayout(self)
        bar = QtWidgets.QWidget()
        bar.setLayout(pills)
        root.addWidget(bar)
        root.addWidget(self._stack, 1)
        self.set_page(0)

    def set_page(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        for i, b in enumerate(self._pills):
            b.setChecked(i == idx)


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
