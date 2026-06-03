"""TradingView-style Economic Calendar tab.

A grouped QTreeWidget (Date header → event rows) fed by a CalendarRepository. Pure-Qt,
dependency-injected repository (tests pass a fake; no network, no modals).
Filters and the live countdown are computed against an injectable `now_ms`.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .calendar_delegate import importance_bar_pixmap, value_color
from ..data.calendar.model import week_start_utc
from ..data.calendar.taxonomy import currency_country

# Common display timezones for the selector (label, tzinfo|None=system local).
_TZ_CHOICES = [
    ("Local", None), ("UTC", timezone.utc),
    ("UTC-8", timezone(timedelta(hours=-8))), ("UTC-5", timezone(timedelta(hours=-5))),
    ("UTC+1", timezone(timedelta(hours=1))), ("UTC+3", timezone(timedelta(hours=3))),
    ("UTC+8", timezone(timedelta(hours=8))),
]

_FLAG_DIR = os.path.join(os.path.dirname(__file__), "resources", "flags")
_COLS = ["Time", "Country", "", "Event", "Actual", "Forecast", "Prior"]


# Simple recognizable flag drawings (no bundled assets): iso2 -> ('h'|'v', [hex bands]).
# Stripe flags are exact; a few iconic ones get a custom painter (below). Anything not here
# falls back to an ISO-code chip — and the Country column always shows the full name too.
_FLAG_BANDS = {
    "de": ("h", ["#000000", "#dd0000", "#ffce00"]),
    "fr": ("v", ["#002395", "#ffffff", "#ed2939"]),
    "it": ("v", ["#009246", "#ffffff", "#ce2b37"]),
    "in": ("h", ["#ff9933", "#ffffff", "#138808"]),
    "ru": ("h", ["#ffffff", "#0039a6", "#d52b1e"]),
    "mx": ("v", ["#006847", "#ffffff", "#ce1126"]),
    "ca": ("v", ["#d52b1e", "#ffffff", "#d52b1e"]),
    "id": ("h", ["#e70011", "#ffffff"]),
    "sg": ("h", ["#ef3340", "#ffffff"]),
    "za": ("h", ["#007a4d", "#ffffff", "#de3831"]),
    "cn": ("h", ["#de2910"]),
    "sa": ("h", ["#006c35"]),
    "hk": ("h", ["#de2910"]),
    "tr": ("h", ["#e30a17"]),
    "au": ("h", ["#012169"]),
    "nz": ("h", ["#012169"]),
}


def _flag_jp(p, w, h):
    p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor("#ffffff"))
    p.setBrush(QtGui.QColor("#bc002d"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(QtCore.QPointF(w / 2, h / 2), h * 0.3, h * 0.3)


def _flag_us(p, w, h):
    p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor("#b22234"))
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor("#ffffff"))
    for i in range(1, 7, 2):
        p.drawRect(QtCore.QRectF(0, i * h / 7, w, h / 7))
    p.fillRect(QtCore.QRectF(0, 0, w * 0.45, h * 0.54), QtGui.QColor("#3c3b6e"))


def _flag_eu(p, w, h):
    p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor("#003399"))
    p.setBrush(QtGui.QColor("#ffcc00"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawEllipse(QtCore.QPointF(w / 2, h / 2), 2.0, 2.0)


def _flag_gb(p, w, h):
    p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor("#012169"))
    pen = QtGui.QPen(QtGui.QColor("#ffffff"))
    pen.setWidthF(2.4)
    p.setPen(pen)
    p.drawLine(QtCore.QLineF(0, 0, w, h))
    p.drawLine(QtCore.QLineF(0, h, w, 0))
    p.drawLine(QtCore.QLineF(w / 2, 0, w / 2, h))
    p.drawLine(QtCore.QLineF(0, h / 2, w, h / 2))
    pen2 = QtGui.QPen(QtGui.QColor("#c8102e"))
    pen2.setWidthF(1.1)
    p.setPen(pen2)
    p.drawLine(QtCore.QLineF(w / 2, 0, w / 2, h))
    p.drawLine(QtCore.QLineF(0, h / 2, w, h / 2))


def _flag_ch(p, w, h):
    p.fillRect(QtCore.QRectF(0, 0, w, h), QtGui.QColor("#d52b1e"))
    p.setBrush(QtGui.QColor("#ffffff"))
    p.setPen(QtCore.Qt.NoPen)
    p.drawRect(QtCore.QRectF(w / 2 - 1.1, h * 0.28, 2.2, h * 0.44))
    p.drawRect(QtCore.QRectF(w * 0.33, h / 2 - 1.1, w * 0.34, 2.2))


_FLAG_SPECIAL = {"jp": _flag_jp, "us": _flag_us, "eu": _flag_eu, "gb": _flag_gb, "ch": _flag_ch}


def _draw_flag(p: QtGui.QPainter, iso: str, w: int, h: int) -> bool:
    """Paint a recognizable flag for *iso*; return False if we don't have one."""
    bands = _FLAG_BANDS.get(iso)
    if bands:
        orient, colors = bands
        n = len(colors)
        for i, c in enumerate(colors):
            r = (QtCore.QRectF(0, i * h / n, w, h / n) if orient == "h"
                 else QtCore.QRectF(i * w / n, 0, w / n, h))
            p.fillRect(r, QtGui.QColor(c))
        return True
    fn = _FLAG_SPECIAL.get(iso)
    if fn:
        fn(p, w, h)
        return True
    return False


def country_chip_pixmap(iso2: str) -> QtGui.QPixmap:
    """A small flag pixmap for *iso2* (e.g. 'us'): a bundled PNG if present, else a
    drawn flag, else a rounded chip with the ISO code. Never crashes on empty/unknown."""
    w, h = 20, 14
    if iso2:
        path = os.path.join(_FLAG_DIR, f"{iso2}.png")
        if os.path.exists(path):
            pm = QtGui.QPixmap(path)
            if not pm.isNull():
                return pm.scaledToHeight(h, QtCore.Qt.SmoothTransformation)
    pm = QtGui.QPixmap(w, h)
    pm.fill(QtCore.Qt.transparent)
    if not iso2:
        return pm
    p = QtGui.QPainter(pm)
    if _draw_flag(p, iso2.lower(), w, h):
        p.setPen(QtGui.QColor(0, 0, 0, 50))      # subtle border around the flag
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRect(0, 0, w - 1, h - 1)
    else:
        p.setPen(QtGui.QColor(theme.TEXT3))
        p.drawRoundedRect(0, 0, w - 1, h - 1, 3, 3)
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
    eventsChanged = QtCore.Signal()   # emitted whenever the week's events change (load / nav)

    def __init__(self, repository=None, parent=None, tz=None):
        super().__init__(parent)
        if repository is None:
            from ..data.calendar.repository import default_repository
            repository = default_repository()
        self._repo = repository
        self._events: list = []
        self._high_only = False
        self._countries: set[str] | None = None      # None = all
        self._tz = tz or datetime.now().astimezone().tzinfo  # display tz (default: system local)
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
        self._toolbar = self._build_toolbar()
        root.addWidget(self._toolbar)   # hidden when embedded in CalendarSpace (controls move to top nav)
        # (day-card strip now owned by CalendarSpace so it can show Economic/Earnings/
        # Dividends/IPO counts together — this tab keeps _day_cards = [] so its
        # _refresh_strip is a guarded no-op.)
        root.addWidget(self._status)
        root.addWidget(self._tree, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        # async-load state: one worker at a time; `_loading_week` is what it's fetching.
        self._worker = None
        self._workers: set = set()
        self._loading_week: int | None = None
        # Wait for any in-flight fetch before the app tears down — a running QThread that is
        # destroyed crashes the process (0xC0000409). aboutToQuit fires before widget teardown.
        _app = QtWidgets.QApplication.instance()
        if _app is not None:
            _app.aboutToQuit.connect(self._stop_workers)

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
        self._btn_countries = self._build_country_button()
        self._cmb_tz = QtWidgets.QComboBox()
        self._cmb_tz.addItems([name for name, _tz in _TZ_CHOICES])
        self._cmb_tz.currentIndexChanged.connect(self._on_tz_changed)
        self._nav_widgets = [self._btn_today, prev, nxt, self._lbl_range]
        for w in self._nav_widgets:
            h.addWidget(w)
        h.addStretch(1)
        h.addWidget(self._chk_high)
        h.addWidget(self._cmb_cat)
        h.addWidget(self._btn_countries)
        h.addWidget(self._cmb_tz)
        return bar

    def _build_country_button(self) -> QtWidgets.QToolButton:
        btn = QtWidgets.QToolButton()
        btn.setText("Countries ▾")
        btn.clicked.connect(self._open_country_dialog)   # TV-style modal (not a checkbox menu)
        return btn

    def _open_country_dialog(self) -> None:
        from .country_dialog import SelectCountriesDialog
        dlg = SelectCountriesDialog(self._countries, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self._countries = dlg.selected_countries()
            self._sync_country_button()
            self._rebuild()

    def _apply_countries(self, currencies: set[str] | None) -> None:
        """Set the country filter programmatically (None == all) and refresh."""
        self._countries = set(currencies) if currencies else None
        self._sync_country_button()
        self._rebuild()

    def _sync_country_button(self) -> None:
        n = len(self._countries) if self._countries else 0
        self._btn_countries.setText("Countries ▾" if not n else f"Countries ({n}) ▾")

    def _on_tz_changed(self, idx: int) -> None:
        self.set_timezone(_TZ_CHOICES[idx][1])

    def set_timezone(self, tz) -> None:
        self._tz = tz or datetime.now().astimezone().tzinfo
        self._refresh_range_label()
        self._rebuild()

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
            title.setStyleSheet(f"color:{theme.TEXT};font-size:13px;font-weight:600;")
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
            dt = self._local(start)
            n = sum(1 for e in self._events if start <= e.ts_utc < start + day_ms)
            title.setText(f"{dt.strftime('%a')} {dt.day}")
            count.setText(f"Economic {n}")

    def _refresh_range_label(self) -> None:
        a = self._local(self._week_start)
        b = self._local(self._week_start + 6 * 24 * 3600 * 1000)
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
        self._update_status()

    _NO_DATA = "No data for this week — ForexFactory provides the current and next week only."

    def _update_status(self) -> None:
        self._status.setText("" if self._events else self._NO_DATA)

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
        prev: dict[str, tuple[str, str]] = {}  # day -> (last time str, last currency)
        now = self._now()
        week_end = self._week_start + 7 * 24 * 3600 * 1000
        # a red "now" marker is shown once, before the first not-yet-happened event,
        # but only when the current time falls inside the displayed week (like TradingView)
        now_done = not (self._week_start <= now < week_end)
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
            if not now_done and ev.ts_utc >= now:
                parent.addChild(self._now_marker(now))
                now_done = True
            # TradingView-exact: show Time once per time-run and the Country flag+name
            # once per consecutive same-country run within the date (blank on continuation).
            t = "" if ev.all_day else self._hhmm(ev.ts_utc)
            last_t, last_c = prev.get(day, (None, None))
            show_time = t != last_t
            show_country = show_time or ev.currency != last_c
            parent.addChild(self._row(ev, show_time=show_time, show_country=show_country))
            prev[day] = (t, ev.currency)
        if self._day_cards:
            self._refresh_strip()
        if hasattr(self, "_lbl_range"):
            self._refresh_range_label()
        self.eventsChanged.emit()

    def _now_marker(self, now: int) -> QtWidgets.QTreeWidgetItem:
        """A full-width red 'now' row (UserRole None, so it isn't treated as an event)."""
        it = QtWidgets.QTreeWidgetItem([f"●  now  {self._hhmm(now)}"])
        it.setFirstColumnSpanned(True)
        it.setData(0, QtCore.Qt.UserRole, None)
        it.setForeground(0, QtGui.QColor(theme.DOWN))
        f = it.font(0)
        f.setBold(True)
        it.setFont(0, f)
        return it

    def _row(self, ev, *, show_time: bool = True, show_country: bool = True) -> QtWidgets.QTreeWidgetItem:
        t = "" if ev.all_day else self._hhmm(ev.ts_utc)
        actual = self.countdown_text(ev.ts_utc) if ev.actual is None and ev.ts_utc > self._now() \
            else ev.actual_display or "—"
        it = QtWidgets.QTreeWidgetItem([t if show_time else "",
                                        ev.country if show_country else "", "", ev.title, actual,
                                        ev.forecast_display or "—", ev.previous_display or "—"])
        it.setData(0, QtCore.Qt.UserRole, ev.id)
        if show_country:
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
            top = self._tree.topLevelItem(i)
            for j in range(top.childCount()):
                if top.child(j).data(0, QtCore.Qt.UserRole) is not None:
                    n += 1     # count event rows only, not the "now" marker
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
        # Reflect the target week IMMEDIATELY (range label, day-card dates, empty table,
        # "Loading…") so navigation feels instant, then fetch off-thread. Only one worker
        # runs at a time; if the user navigates again mid-load, the finished handler picks
        # up the newest week (latest-nav-wins).
        self._show_loading()
        self._force = force
        if self._loading_week is None:
            self._start_worker()

    def _start_worker(self) -> None:
        self._loading_week = self._week_start
        worker = _CalendarFetchWorker(self._repo, self._week_start, force=getattr(self, "_force", False))
        worker.eventsReady.connect(self._on_events)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        self._workers.add(worker)
        worker.start()

    def _on_events(self, events) -> None:
        # apply only if the fetched week is still the one the user is looking at
        if self._loading_week == self._week_start:
            self._events = events
            self._rebuild()
            self._update_status()

    def _on_failed(self, msg) -> None:
        if self._loading_week == self._week_start:
            self._status.setText(f"Calendar error: {msg}")

    def _on_worker_finished(self) -> None:
        self._workers.discard(self.sender())
        done = self._loading_week
        self._loading_week = None
        if done != self._week_start:          # user navigated during the load → fetch the new week
            self._start_worker()

    def _show_loading(self) -> None:
        self._refresh_range_label()
        day_ms = 24 * 3600 * 1000
        for i, (_card, title, count) in enumerate(self._day_cards):
            dt = self._local(self._week_start + i * day_ms)
            title.setText(f"{dt.strftime('%a')} {dt.day}")
            count.setText("…")
        self._tree.clear()
        self._status.setText("Loading…")

    def _local(self, ts_ms: int) -> datetime:
        """Epoch ms → datetime in the selected display timezone (default: system local)."""
        return datetime.fromtimestamp(ts_ms / 1000, tz=self._tz)

    def _stop_workers(self) -> None:
        # give an in-flight fetch a moment, then force-stop so the QThread is never
        # destroyed mid-run (which crashes the process at exit)
        for w in list(self._workers):
            try:
                if w.isRunning() and not w.wait(3000):
                    w.terminate()
                    w.wait(1000)
            except RuntimeError:
                pass

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

    def _date_header(self, ts_utc: int) -> str:
        # NB: %-d / %#d are not portable across OSes — build the day number manually.
        dt = self._local(ts_utc)
        return f"{dt.strftime('%A, %B')} {dt.day}"

    def _hhmm(self, ts_utc: int) -> str:
        return self._local(ts_utc).strftime("%H:%M")
