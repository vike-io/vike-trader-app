"""Journal tab — record dated notes against strategies/symbols (file-backed via analysis.journal)."""

import time
from datetime import datetime, timezone

from PySide6 import QtCore, QtWidgets

from ..analysis.journal import Journal, JournalEntry

_COLS = ["Date (UTC)", "Title", "Symbol", "Strategy", "Notes"]


class JournalTab(QtWidgets.QWidget):
    """An add-entry form over a persisted table of journal notes."""

    def __init__(self, journal: Journal | None = None, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._journal = journal if journal is not None else Journal()
        self._display_indices: list[int] = []   # display row -> store index (set by _refresh)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        form = QtWidgets.QHBoxLayout()
        self._title = QtWidgets.QLineEdit()
        self._title.setPlaceholderText("Title *")
        self._symbol = QtWidgets.QLineEdit()
        self._symbol.setPlaceholderText("Symbol")
        self._symbol.setMaximumWidth(120)
        self._strategy = QtWidgets.QLineEdit()
        self._strategy.setPlaceholderText("Strategy")
        self._strategy.setMaximumWidth(160)
        self._btn_add = QtWidgets.QPushButton("Add entry")
        self._btn_add.setObjectName("play")
        self._btn_add.clicked.connect(self._add)
        form.addWidget(self._title, 2)
        form.addWidget(self._symbol)
        form.addWidget(self._strategy)
        form.addWidget(self._btn_add)
        root.addLayout(form)

        self._notes = QtWidgets.QPlainTextEdit()
        self._notes.setPlaceholderText("Notes …")
        self._notes.setMaximumHeight(70)
        root.addWidget(self._notes)

        self._table = QtWidgets.QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self._table, 1)

        self._btn_del = QtWidgets.QPushButton("Remove selected")
        self._btn_del.clicked.connect(self._remove)
        root.addWidget(self._btn_del, 0, QtCore.Qt.AlignRight)

        self._refresh()

    def _add(self) -> None:
        title = self._title.text().strip()
        if not title:
            return
        self._journal.add(JournalEntry(
            ts=int(time.time() * 1000), title=title,
            symbol=self._symbol.text().strip(), strategy=self._strategy.text().strip(),
            notes=self._notes.toPlainText().strip(),
        ))
        for w in (self._title, self._symbol, self._strategy):
            w.clear()
        self._notes.clear()
        self._refresh()

    def _remove(self) -> None:
        row = self._table.currentRow()
        if 0 <= row < len(self._display_indices):
            self._journal.remove(self._display_indices[row])   # exact store index, no ts/title match
            self._refresh()

    def _refresh(self) -> None:
        indexed = self._journal.entries_indexed()
        self._display_indices = [i for i, _ in indexed]        # display row -> store index
        self._table.setRowCount(len(indexed))
        for r, (_, e) in enumerate(indexed):
            dt = datetime.fromtimestamp(e.ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            cells = [dt, e.title, e.symbol, e.strategy, e.notes.replace("\n", " ")]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 0:
                    item.setForeground(QtCore.Qt.GlobalColor.gray)
                self._table.setItem(r, c, item)
