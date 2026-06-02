"""TradingView-style Economic Calendar tab.

A grouped QTreeWidget (Date header → event rows) fed by a CalendarRepository. Pure-Qt,
dependency-injected repository (tests pass a fake; no network, no modals).
Filters and the live countdown are computed against an injectable `now_ms`.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .calendar_delegate import importance_bar_pixmap, value_color
from ..data.calendar.model import week_start_utc
from ..data.calendar.taxonomy import currency_country

_FLAG_DIR = os.path.join(os.path.dirname(__file__), "resources", "flags")
_COLS = ["Time", "Country", "", "Event", "Actual", "Forecast", "Prior"]


def country_chip_pixmap(iso2: str) -> QtGui.QPixmap:
    """Return a flag pixmap for *iso2* (e.g. 'us') if a PNG asset exists under
    resources/flags/, otherwise paint a small rounded chip with the ISO code.
    Never crashes on empty/unknown iso — returns a blank transparent pixmap."""
    if iso2:
        path = os.path.join(_FLAG_DIR, f"{iso2}.png")
        if os.path.exists(path):
            pm = QtGui.QPixmap(path)
            if not pm.isNull():
                return pm.scaledToHeight(14, QtCore.Qt.SmoothTransformation)
    pm = QtGui.QPixmap(20, 14)
    pm.fill(QtCore.Qt.transparent)
    if iso2:
        p = QtGui.QPainter(pm)
        p.setPen(QtGui.QColor(theme.TEXT3))
        p.drawRoundedRect(0, 0, 19, 13, 3, 3)
        f = p.font()
        f.setPointSize(6)
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, iso2.upper())
        p.end()
    return pm


class _CalendarFetchWorker(QtCore.QThread):
    """Off-thread week fetch (network + JSON only — safe off the UI thread, like
    app._LiveFetchWorker). Results marshal back via signals; the UI never blocks."""
    eventsReady = QtCore.Signal(object)   # list[CalendarEvent]
    failed = QtCore.Signal(str)

    def __init__(self, repo, week_start_ms: int, *, force: bool = False):
        super().__init__()
        self._repo, self._ws, self._force = repo, week_start_ms, force

    def run(self):
        try:
            self.eventsReady.emit(self._repo.get_week(self._ws, force=self._force))
        except Exception as exc:  # noqa: BLE001 - surfaced to a status label, never a modal
            self.failed.emit(str(exc))


class EconomicCalendarTab(QtWidgets.QWidget):
    def __init__(self, repository=None, parent=None):
        super().__init__(parent)
        if repository is None:
            from ..data.calendar.repository import default_repository
            repository = default_repository()
        self._repo = repository
        self._events: list = []
        self._high_only = False
        self._countries: set[str] | None = None      # None = all
        self._now = lambda: int(time.time() * 1000)
        self._week_start = week_start_utc(self._now())
        self._loaded = False
        # defaults must be set BEFORE _build_toolbar() which can trigger _rebuild
        self._category = "All"
        self._day_cards: list = []

        root = QtWidgets.QVBoxLayout(self)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setColumnCount(len(_COLS))
        self._tree.setHeaderLabels(_COLS)
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        self._tree.setAlternatingRowColors(False)
        self._tree.header().setSectionResizeMode(3, QtWidgets.QHeaderView.Stretch)
        self._tree.itemClicked.connect(lambda it, _c: self._toggle_detail(it))
        root.addWidget(self._build_toolbar())
        root.addWidget(self._build_week_strip())
        root.addWidget(self._status)
        root.addWidget(self._tree, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._worker = None

    # ---- toolbar ----
    def _build_toolbar(self) -> QtWidgets.QWidget:
        bar = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        self._btn_today = QtWidgets.QPushButton("Today")
        self._btn_today.clicked.connect(self.go_today)
        prev = QtWidgets.QPushButton("‹")
        prev.clicked.connect(self.go_prev_week)
        nxt = QtWidgets.QPushButton("›")
        nxt.clicked.connect(self.go_next_week)
        self._lbl_range = QtWidgets.QLabel("")
        self._chk_high = QtWidgets.QCheckBox("High only")
        self._chk_high.toggled.connect(self.set_high_only)
        self._cmb_cat = QtWidgets.QComboBox()
        self._cmb_cat.addItems(
            ["All", "rates", "inflation", "employment", "gdp", "trade", "housing", "other"]
        )
        self._cmb_cat.currentTextChanged.connect(self.set_category)
        for w in (self._btn_today, prev, nxt, self._lbl_range):
            h.addWidget(w)
        h.addStretch(1)
        h.addWidget(self._chk_high)
        h.addWidget(self._cmb_cat)
        return bar

    # ---- week strip ----
    def _build_week_strip(self) -> QtWidgets.QWidget:
        self._strip = QtWidgets.QWidget()
        self._strip_layout = QtWidgets.QHBoxLayout(self._strip)
        self._strip_layout.setContentsMargins(0, 0, 0, 0)
        self._day_cards = []          # list[tuple[QFrame, QLabel title, QLabel count]]
        for _ in range(7):
            card = QtWidgets.QFrame()
            card.setProperty("class", "Panel")
            v = QtWidgets.QVBoxLayout(card)
            title = QtWidgets.QLabel("")
            title.setStyleSheet(f"color:{theme.TEXT};font-weight:600;")
            count = QtWidgets.QLabel("")
            count.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
            v.addWidget(title)
            v.addWidget(count)
            self._day_cards.append((card, title, count))
            self._strip_layout.addWidget(card)
        return self._strip

    def day_card_count(self) -> int:
        return len(self._day_cards)

    def _refresh_strip(self) -> None:
        """Fill the 7 day-cards with weekday+date and that day's event count."""
        day_ms = 24 * 3600 * 1000
        for i, (_card, title, count) in enumerate(self._day_cards):
            start = self._week_start + i * day_ms
            dt = datetime.fromtimestamp(start / 1000, tz=timezone.utc)
            n = sum(1 for e in self._events if start <= e.ts_utc < start + day_ms)
            title.setText(f"{dt.strftime('%a')} {dt.day}")
            count.setText(f"Economic {n}")

    def _refresh_range_label(self) -> None:
        a = datetime.fromtimestamp(self._week_start / 1000, tz=timezone.utc)
        b = datetime.fromtimestamp(
            (self._week_start + 6 * 24 * 3600 * 1000) / 1000, tz=timezone.utc
        )
        self._lbl_range.setText(
            f"{a.strftime('%b')} {a.day} — {b.strftime('%b')} {b.day}, {b.year}"
        )

    # ---- week navigation ----
    def current_week_start(self) -> int:
        return self._week_start

    def go_today(self) -> None:
        self._week_start = week_start_utc(self._now())
        self.refresh_async()

    def go_prev_week(self) -> None:
        self._week_start -= 7 * 24 * 3600 * 1000
        self.refresh_async()

    def go_next_week(self) -> None:
        self._week_start += 7 * 24 * 3600 * 1000
        self.refresh_async()

    def showEvent(self, event):  # noqa: N802 - Qt override: load the week when the space is first opened
        super().showEvent(event)
        if not self._loaded:
            self._loaded = True
            self.refresh_async()

    # ---- data ----
    def load_week(self, week_start_ms: int) -> None:
        self._week_start = week_start_ms
        self._events = self._repo.get_week(week_start_ms)
        self._rebuild()

    def _passes(self, ev) -> bool:
        if self._high_only and ev.importance < 2:
            return False
        if self._countries is not None and ev.currency not in self._countries:
            return False
        if self._category != "All" and ev.category != self._category:
            return False
        return True

    def _rebuild(self) -> None:
        self._tree.clear()
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for ev in sorted(self._events, key=lambda e: (e.ts_utc, e.country, e.title)):
            if not self._passes(ev):
                continue
            day = self._date_header(ev.ts_utc)
            parent = groups.get(day)
            if parent is None:
                parent = QtWidgets.QTreeWidgetItem([day])
                parent.setFirstColumnSpanned(True)
                f = parent.font(0)
                f.setBold(True)
                parent.setFont(0, f)
                self._tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                groups[day] = parent
            parent.addChild(self._row(ev))
        if self._day_cards:
            self._refresh_strip()
        if hasattr(self, "_lbl_range"):
            self._refresh_range_label()

    def _row(self, ev) -> QtWidgets.QTreeWidgetItem:
        t = "" if ev.all_day else self._hhmm(ev.ts_utc)
        actual = self.countdown_text(ev.ts_utc) if ev.actual is None and ev.ts_utc > self._now() \
            else ev.actual_display or "—"
        it = QtWidgets.QTreeWidgetItem([t, ev.country, "", ev.title, actual,
                                        ev.forecast_display or "—", ev.previous_display or "—"])
        it.setData(0, QtCore.Qt.UserRole, ev.id)
        _country, iso = currency_country(ev.currency)
        it.setIcon(1, QtGui.QIcon(country_chip_pixmap(iso)))
        it.setIcon(2, QtGui.QIcon(importance_bar_pixmap(ev.importance)))
        color = theme.DOWN if (ev.actual is None and ev.ts_utc > self._now()) else value_color(ev.actual, ev.forecast)
        it.setForeground(4, QtGui.QColor(color))
        return it

    # ---- filters ----
    def set_high_only(self, on: bool) -> None:
        self._high_only = on
        self._rebuild()

    def set_countries(self, currencies: set[str] | None) -> None:
        self._countries = currencies
        self._rebuild()

    def set_category(self, cat: str) -> None:
        self._category = cat
        self._rebuild()

    def visible_event_count(self) -> int:
        n = 0
        for i in range(self._tree.topLevelItemCount()):
            n += self._tree.topLevelItem(i).childCount()
        return n

    # ---- time helpers ----
    def set_now_ms(self, ms: int) -> None:
        self._now = lambda: ms
        self._rebuild()

    def countdown_text(self, ts_utc: int) -> str:
        secs = max(0, (ts_utc - self._now()) // 1000)
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        return f"Coming in {h}:{m:02d}:{s:02d}"

    # ---- async load ----
    def refresh_async(self, *, force: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # a fetch is already in flight; the repository rate-limit handles freshness
        self._status.setText("Loading…")
        self._worker = _CalendarFetchWorker(self._repo, self._week_start, force=force)
        self._worker.eventsReady.connect(self._on_events)
        self._worker.failed.connect(lambda msg: self._status.setText(f"Calendar error: {msg}"))
        self._worker.start()

    def _on_events(self, events) -> None:
        self._events = events
        self._status.setText("")
        self._rebuild()

    def _tick(self) -> None:
        # cheap: only touch rows that show a countdown (future, no actual)
        now = self._now()
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            for j in range(top.childCount()):
                row = top.child(j)
                ev_id = row.data(0, QtCore.Qt.UserRole)
                ev = next((e for e in self._events if e.id == ev_id), None)
                if ev and ev.actual is None and ev.ts_utc > now:
                    row.setText(4, self.countdown_text(ev.ts_utc))

    # ---- detail row ----
    def _toggle_detail(self, row) -> None:
        ev_id = row.data(0, QtCore.Qt.UserRole)
        if ev_id is None:                      # date header or a detail node itself
            return
        if row.childCount():
            row.takeChildren()
            return
        ev = next((e for e in self._events if e.id == ev_id), None)
        if ev is None:
            return
        text = (f"{ev.title} · {ev.country}  |  "
                f"Actual {ev.actual_display or '—'} · "
                f"Forecast {ev.forecast_display or '—'} · "
                f"Prior {ev.previous_display or '—'}"
                + (f"  ·  actual via {ev.actual_source}" if ev.actual_source else ""))
        detail = QtWidgets.QTreeWidgetItem([text])
        detail.setFirstColumnSpanned(True)
        detail.setForeground(0, QtGui.QColor(theme.TEXT2))
        row.addChild(detail)
        row.setExpanded(True)

    @staticmethod
    def _date_header(ts_utc: int) -> str:
        # NB: %-d / %#d are not portable across OSes — build the day number manually.
        dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
        return f"{dt.strftime('%A, %B')} {dt.day}"

    @staticmethod
    def _hhmm(ts_utc: int) -> str:
        return datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc).strftime("%H:%M")
