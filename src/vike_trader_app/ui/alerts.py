"""Alerts tab — saved local watchlist alerts, checked against the cache (reuses the screener).

Add ``(symbol, rule, notify-on)`` alerts; "Check now" loads the referenced symbols' closes
READ-ONLY via the Catalog and flags which alerts fire. Persisted via analysis.alerts.AlertStore.
"""

from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis.alerts import AlertRule, AlertStore, evaluate
from ..analysis.screener import RULES
from . import theme

_COLS = ["Symbol", "Rule", "Notify on", "Status", "Signal", "Value"]


class AlertsTab(QtWidgets.QWidget):
    """Add-alert form + 'Check now' over a persisted, colour-coded alert table."""

    def __init__(self, store: AlertStore | None = None, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._store = store if store is not None else AlertStore()
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        form = QtWidgets.QHBoxLayout()
        self._symbol = QtWidgets.QComboBox()
        self._symbol.setEditable(True)
        self._symbol.setMinimumWidth(120)
        self._rule = QtWidgets.QComboBox()
        for r in RULES:
            self._rule.addItem(r.name)
        self._dir = QtWidgets.QComboBox()
        self._dir.addItems(["any", "long", "short"])
        self._btn_add = QtWidgets.QPushButton("Add alert")
        self._btn_add.clicked.connect(self._add)
        self._btn_check = QtWidgets.QPushButton("Check now")
        self._btn_check.setObjectName("play")
        self._btn_check.clicked.connect(self.check)
        for w in (QtWidgets.QLabel("Symbol:"), self._symbol, QtWidgets.QLabel("Rule:"), self._rule,
                  QtWidgets.QLabel("Notify:"), self._dir, self._btn_add, self._btn_check):
            form.addWidget(w)
        form.addStretch(1)
        root.addLayout(form)

        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        root.addWidget(self._status)

        self._table = QtWidgets.QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        root.addWidget(self._table, 1)

        self._btn_del = QtWidgets.QPushButton("Remove selected")
        self._btn_del.clicked.connect(self._remove)
        root.addWidget(self._btn_del, 0, QtCore.Qt.AlignRight)

        self._populate_symbols()
        self._refresh()

    def _catalog(self):
        from ..data.catalog import Catalog
        return Catalog()

    def _populate_symbols(self) -> None:
        try:
            syms = self._catalog().symbols()
        except Exception:  # noqa: BLE001 - empty/missing cache
            syms = []
        self._symbol.clear()
        self._symbol.addItems(syms)

    def _add(self) -> None:
        sym = self._symbol.currentText().strip()
        if not sym:
            return
        self._store.add(AlertRule(symbol=sym, rule=self._rule.currentText(),
                                  direction=self._dir.currentText()))
        self._refresh()

    def _remove(self) -> None:
        row = self._table.currentRow()
        if 0 <= row < len(self._store.rules()):
            self._store.remove(row)
            self._refresh()

    def check(self) -> None:
        """Evaluate every alert against the cached closes for its symbol; flag triggers."""
        rules = self._store.rules()
        if not rules:
            self._status.setText("No alerts yet — add one above.")
            return
        cat = self._catalog()
        closes: dict[str, list] = {}
        for sym in {r.symbol for r in rules}:
            ivals = cat.intervals(sym)
            iv = "1m" if "1m" in ivals else (ivals[0] if ivals else None)
            closes[sym] = [b.close for b in cat.query(sym, iv)] if iv else []
        hits = evaluate(rules, closes)
        self._refresh(hits)
        n = sum(1 for h in hits if h.triggered)
        self._status.setText(f"{n} of {len(hits)} alert(s) triggered")

    def _refresh(self, hits=None) -> None:
        rules = self._store.rules()
        self._table.setRowCount(len(rules))
        for r, ar in enumerate(rules):
            hit = hits[r] if hits and r < len(hits) else None
            status = ("TRIGGERED" if hit.triggered else "quiet") if hit else "—"
            signal = hit.signal if hit else ""
            value = f"{hit.value:,.2f}" if hit else ""
            cells = [ar.symbol, ar.rule, ar.direction, status, signal, value]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 3 and hit:
                    item.setForeground(QtGui.QColor(theme.UP if hit.triggered else theme.TEXT3))
                elif c == 4 and signal:
                    item.setForeground(QtGui.QColor(
                        {"long": theme.UP, "short": theme.DOWN}.get(signal, theme.TEXT3)))
                self._table.setItem(r, c, item)
