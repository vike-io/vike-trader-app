"""TradingView-style Economic Calendar tab.

A grouped QTreeWidget (Date header → event rows) fed by a CalendarRepository. Pure-Qt,
dependency-injected repository (tests pass a fake; no network, no modals). Filters,
live countdown and the now-line are computed against an injectable `now_ms`.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme
from .calendar_delegate import importance_bar_pixmap, value_color
from ..data.calendar.model import week_start_utc

_COLS = ["Time", "Country", "", "Event", "Actual", "Forecast", "Prior"]


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
        root.addWidget(self._status)
        root.addWidget(self._tree, 1)

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

    def _row(self, ev) -> QtWidgets.QTreeWidgetItem:
        t = "" if ev.all_day else self._hhmm(ev.ts_utc)
        actual = self.countdown_text(ev.ts_utc) if ev.actual is None and ev.ts_utc > self._now() \
            else ev.actual_display or "—"
        it = QtWidgets.QTreeWidgetItem([t, ev.country, "", ev.title, actual,
                                        ev.forecast_display or "—", ev.previous_display or "—"])
        it.setData(0, QtCore.Qt.UserRole, ev.id)
        it.setIcon(2, QtGui.QIcon(importance_bar_pixmap(ev.importance)))
        it.setForeground(4, QtGui.QColor(value_color(ev.actual, ev.forecast)))
        if ev.actual is None and ev.ts_utc > self._now():
            it.setForeground(4, QtGui.QColor(theme.DOWN))   # red "Coming in …"
        return it

    # ---- filters (return nothing; trigger a rebuild) ----
    def set_high_only(self, on: bool) -> None:
        self._high_only = on
        self._rebuild()

    def set_countries(self, currencies: set[str] | None) -> None:
        self._countries = currencies
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

    @staticmethod
    def _date_header(ts_utc: int) -> str:
        # NB: %-d / %#d are not portable across OSes — build the day number manually.
        dt = datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc)
        return f"{dt.strftime('%A, %B')} {dt.day}"

    @staticmethod
    def _hhmm(ts_utc: int) -> str:
        return datetime.fromtimestamp(ts_utc / 1000, tz=timezone.utc).strftime("%H:%M")
