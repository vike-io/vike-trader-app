"""Screener tab — scan the cached symbol universe with an indicator rule, ranked long/short.

Reads the local Parquet cache READ-ONLY via ``data.catalog.Catalog``; the ranking logic lives in
``analysis.screener``. A scan loads each symbol's closes, applies the chosen rule, and fills a
sorted, colour-coded table (longs grouped first).
"""

from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis.screener import RULES, screen
from . import theme

_COLS = ["Symbol", "Signal", "Value", "Last"]


class ScreenerTab(QtWidgets.QWidget):
    """Rule dropdown + interval + Scan, over a colour-coded results table."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        self._rule = QtWidgets.QComboBox()
        for r in RULES:
            self._rule.addItem(r.name, r)
        self._rule.currentIndexChanged.connect(self._on_rule_changed)
        self._interval = QtWidgets.QComboBox()
        self._btn_scan = QtWidgets.QPushButton("Scan universe")
        self._btn_scan.setObjectName("play")
        self._btn_scan.clicked.connect(self.scan)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        bar.addWidget(QtWidgets.QLabel("Rule:"))
        bar.addWidget(self._rule)
        bar.addWidget(QtWidgets.QLabel("Interval:"))
        bar.addWidget(self._interval)
        bar.addWidget(self._btn_scan)
        bar.addWidget(self._status, 1)
        root.addLayout(bar)

        self._desc = QtWidgets.QLabel(RULES[0].description if RULES else "")
        self._desc.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;")
        root.addWidget(self._desc)

        self._table = QtWidgets.QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        root.addWidget(self._table, 1)

        self._populate_intervals()

    def _catalog(self):
        from ..data.catalog import Catalog
        return Catalog()

    def _on_rule_changed(self) -> None:
        rule = self._rule.currentData()
        self._desc.setText(rule.description if rule else "")

    def _populate_intervals(self) -> None:
        try:
            cat = self._catalog()
            ivals = sorted({iv for s in cat.symbols() for iv in cat.intervals(s)})
        except Exception:  # noqa: BLE001 - missing/empty cache -> default
            ivals = []
        self._interval.clear()
        self._interval.addItems(ivals or ["1m"])
        i = self._interval.findText("1m")
        if i >= 0:
            self._interval.setCurrentIndex(i)

    def scan(self) -> None:
        """Load every cached symbol for the interval, run the rule, fill the table."""
        cat = self._catalog()
        interval = self._interval.currentText()
        syms = [s for s in cat.symbols() if interval in cat.intervals(s)]
        if not syms:
            self._table.setRowCount(0)
            self._status.setText("No cached data for this interval — fetch some symbols first.")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            closes = {s: [b.close for b in cat.query(s, interval)] for s in syms}
            rows = screen(closes, self._rule.currentData())
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._fill(rows)
        n_long = sum(1 for r in rows if r.signal == "long")
        n_short = sum(1 for r in rows if r.signal == "short")
        self._status.setText(f"{len(rows)} symbols · {n_long} long · {n_short} short · {interval}")

    def _fill(self, rows) -> None:
        colors = {"long": theme.UP, "short": theme.DOWN, "neutral": theme.TEXT3}
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [row.symbol, row.signal.upper(), f"{row.value:,.2f}", f"{row.last:,.5g}"]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 1:
                    item.setForeground(QtGui.QColor(colors.get(row.signal, theme.TEXT)))
                if c >= 2:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self._table.setItem(r, c, item)
