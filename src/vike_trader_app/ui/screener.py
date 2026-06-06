"""Screener tab — scan the cached symbol universe with an indicator rule, ranked long/short.

Reads the local Parquet cache READ-ONLY via ``data.catalog.Catalog``; the ranking logic lives in
``analysis.screener``. A scan loads each symbol's bars, applies the chosen rule (a base rule or a
user-built AND/OR composite), and fills a sorted, colour-coded table (longs grouped first).

Enrichments over the basic scan: multi-condition composite rules (built in a small dialog, saved to
disk), a minimum-average-volume liquidity filter, near-real-time auto-rescan (a MAIN-THREAD QTimer —
the data layer is not thread-safe, so we never read Parquet off-thread), and CSV export.
"""

import csv
import statistics

from PySide6 import QtCore, QtGui, QtWidgets

from ..analysis.screener import (
    RULES,
    CompositeRule,
    CompositeStore,
    Condition,
    composites,
    screen,
)
from . import theme

_COLS = ["Symbol", "Signal", "Value", "Last", "Avg Vol"]


class RuleBuilderDialog(QtWidgets.QDialog):
    """Compose an AND/OR multi-condition rule from the base rules.

    Each condition is a base ``ScreenRule`` + the signal direction it must emit; the composite fires
    its chosen direction when ALL (AND) or ANY (OR) conditions hold.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("New composite rule")
        self.setModal(True)
        self.setMinimumWidth(460)
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        eyebrow = QtWidgets.QLabel("SCREENER")
        eyebrow.setStyleSheet(f"color:{theme.ACCENT};font-size:10px;font-weight:700;letter-spacing:2px;")
        title = QtWidgets.QLabel("Multi-condition rule")
        title.setStyleSheet(f"color:{theme.TEXT};font-size:18px;font-weight:700;")
        root.addWidget(eyebrow)
        root.addWidget(title)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.name = QtWidgets.QLineEdit()
        self.name.setPlaceholderText("e.g. Oversold + uptrend")
        self.combine = QtWidgets.QComboBox()
        self.combine.addItems(["AND", "OR"])
        self.direction = QtWidgets.QComboBox()
        self.direction.addItems(["long", "short"])
        form.addRow("Name", self.name)
        form.addRow("Combine", self.combine)
        form.addRow("Emit signal", self.direction)
        root.addLayout(form)

        cond_cap = QtWidgets.QLabel("CONDITIONS")
        cond_cap.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;")
        root.addWidget(cond_cap)
        self._cond_host = QtWidgets.QVBoxLayout()
        self._cond_host.setSpacing(6)
        host = QtWidgets.QWidget()
        host.setLayout(self._cond_host)
        root.addWidget(host)
        self._cond_rows: list[tuple[QtWidgets.QComboBox, QtWidgets.QComboBox]] = []
        self._add_condition()
        self._add_condition()

        add_btn = QtWidgets.QPushButton("+ condition")
        add_btn.clicked.connect(self._add_condition)
        root.addWidget(add_btn, 0, QtCore.Qt.AlignLeft)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Cancel)
        btns.addButton("Create", QtWidgets.QDialogButtonBox.AcceptRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _add_condition(self) -> None:
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        rule = QtWidgets.QComboBox()
        rule.addItems([r.name for r in RULES])
        direction = QtWidgets.QComboBox()
        direction.addItems(["long", "short", "neutral"])
        row.addWidget(rule, 1)
        row.addWidget(direction)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        self._cond_host.addWidget(w)
        self._cond_rows.append((rule, direction))

    def rule(self) -> CompositeRule | None:
        """Build the CompositeRule from the form, or None when unnamed / no conditions."""
        name = self.name.text().strip()
        if not name or not self._cond_rows:
            return None
        conds = tuple(
            Condition(rule=rc.currentText(), direction=dc.currentText())
            for rc, dc in self._cond_rows
        )
        combine = self.combine.currentText()
        emit = self.direction.currentText()
        desc = f"{combine} of {len(conds)} conditions → {emit}"
        return CompositeRule(name=name, description=desc, conditions=conds, combine=combine, direction=emit)


class ScreenerTab(QtWidgets.QWidget):
    """Rule dropdown + interval + Scan over a colour-coded table, with composites / volume / live / export."""

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._store = self._make_store()  # loads + registers saved composites

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Row 1 — rule + builder + interval + scan + status
        bar = QtWidgets.QHBoxLayout()
        self._rule = QtWidgets.QComboBox()
        self._rule.currentIndexChanged.connect(self._on_rule_changed)
        self._btn_new_rule = QtWidgets.QPushButton("+ Rule")
        self._btn_new_rule.setToolTip("Build a multi-condition (AND/OR) rule")
        self._btn_new_rule.clicked.connect(self._new_rule)
        self._interval = QtWidgets.QComboBox()
        self._btn_scan = QtWidgets.QPushButton("Scan universe")
        self._btn_scan.setObjectName("play")
        self._btn_scan.clicked.connect(self.scan)
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        bar.addWidget(QtWidgets.QLabel("Rule:"))
        bar.addWidget(self._rule)
        bar.addWidget(self._btn_new_rule)
        bar.addWidget(QtWidgets.QLabel("Interval:"))
        bar.addWidget(self._interval)
        bar.addWidget(self._btn_scan)
        bar.addWidget(self._status, 1)
        root.addLayout(bar)

        # Row 2 — liquidity filter + live auto-rescan + export
        bar2 = QtWidgets.QHBoxLayout()
        self._min_vol = QtWidgets.QDoubleSpinBox()
        self._min_vol.setRange(0.0, 1e12)
        self._min_vol.setDecimals(0)
        self._min_vol.setGroupSeparatorShown(True)
        self._min_vol.setToolTip("Drop symbols whose mean bar volume is below this (0 = off)")
        self._live = QtWidgets.QCheckBox("Live")
        self._live.setToolTip("Auto-rescan the cache on a timer (main thread, read-only)")
        self._live.toggled.connect(self._on_live_toggled)
        self._live_secs = QtWidgets.QSpinBox()
        self._live_secs.setRange(5, 600)
        self._live_secs.setValue(15)
        self._live_secs.setSuffix(" s")
        self._live_secs.valueChanged.connect(self._on_secs_changed)
        self._btn_export = QtWidgets.QPushButton("Export CSV")
        self._btn_export.clicked.connect(self._export_csv)
        bar2.addWidget(QtWidgets.QLabel("Min vol:"))
        bar2.addWidget(self._min_vol)
        bar2.addStretch(1)
        bar2.addWidget(self._live)
        bar2.addWidget(self._live_secs)
        bar2.addWidget(self._btn_export)
        root.addLayout(bar2)

        self._desc = QtWidgets.QLabel("")
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

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.scan)

        self._refresh_rules()
        self._populate_intervals()

    # --- collaborators (overridable in tests) ---

    def _catalog(self):
        from ..data.catalog import Catalog
        return Catalog()

    def _make_store(self) -> CompositeStore:
        return CompositeStore()

    # --- rules ---

    def _refresh_rules(self) -> None:
        """Repopulate the rule dropdown with the base rules + any saved/registered composites."""
        current = self._rule.currentText()
        self._rule.blockSignals(True)
        self._rule.clear()
        for r in RULES:
            self._rule.addItem(r.name, r)
        for c in composites():
            self._rule.addItem(c.name, c)
        self._rule.blockSignals(False)
        i = self._rule.findText(current)
        self._rule.setCurrentIndex(i if i >= 0 else 0)
        self._on_rule_changed()

    def _on_rule_changed(self) -> None:
        rule = self._rule.currentData()
        self._desc.setText(getattr(rule, "description", "") if rule else "")

    def _new_rule(self) -> None:
        dlg = RuleBuilderDialog(parent=self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            rule = dlg.rule()
            if rule is None:
                self._status.setText("Name the rule and add at least one condition.")
                return
            self._store.add(rule)          # registers + persists
            self._refresh_rules()
            i = self._rule.findText(rule.name)
            if i >= 0:
                self._rule.setCurrentIndex(i)

    # --- intervals ---

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

    # --- scan ---

    def scan(self) -> None:
        """Load every cached symbol for the interval, run the rule (+ volume filter), fill the table."""
        cat = self._catalog()
        interval = self._interval.currentText()
        try:
            syms = [s for s in cat.symbols() if interval in cat.intervals(s)]
        except Exception as exc:  # noqa: BLE001 - a bad/locked cache must not escape the live timer slot
            self._table.setRowCount(0)
            self._status.setText(f"Scan failed: {exc}")
            return
        if not syms:
            self._table.setRowCount(0)
            self._status.setText("No cached data for this interval — fetch some symbols first.")
            return
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            bars_by = {s: cat.query(s, interval) for s in syms}
            closes = {s: [b.close for b in bars] for s, bars in bars_by.items()}
            volumes = {s: [b.volume for b in bars] for s, bars in bars_by.items()}
            min_vol = self._min_vol.value()
            rows = screen(closes, self._rule.currentData(), symbol_volumes=volumes, min_volume=min_vol)
        except Exception as exc:  # noqa: BLE001 - a corrupt/locked shard mid-write must not crash the timer
            self._status.setText(f"Scan failed: {exc}")
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        avg_vol = {s: (statistics.fmean(v) if v else 0.0) for s, v in volumes.items()}
        self._fill(rows, avg_vol)
        n_long = sum(1 for r in rows if r.signal == "long")
        n_short = sum(1 for r in rows if r.signal == "short")
        live = " · live" if self._timer.isActive() else ""
        self._status.setText(f"{len(rows)} symbols · {n_long} long · {n_short} short · {interval}{live}")

    def _fill(self, rows, avg_vol: dict) -> None:
        colors = {"long": theme.UP, "short": theme.DOWN, "neutral": theme.TEXT3}
        self._table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            av = avg_vol.get(row.symbol, 0.0)
            cells = [row.symbol, row.signal.upper(), f"{row.value:,.2f}", f"{row.last:,.5g}", f"{av:,.0f}"]
            for c, text in enumerate(cells):
                item = QtWidgets.QTableWidgetItem(text)
                if c == 1:
                    item.setForeground(QtGui.QColor(colors.get(row.signal, theme.TEXT)))
                if c >= 2:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self._table.setItem(r, c, item)

    # --- live auto-rescan (MAIN-THREAD timer; data layer is not thread-safe) ---

    def _on_live_toggled(self, on: bool) -> None:
        if on:
            self.scan()
            self._timer.start(self._live_secs.value() * 1000)
        else:
            self._timer.stop()

    def _on_secs_changed(self, _v: int) -> None:
        if self._timer.isActive():
            self._timer.start(self._live_secs.value() * 1000)  # restart at the new cadence

    def hideEvent(self, e):  # noqa: N802 - stop background cache reads when the tab isn't visible
        self._timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):  # noqa: N802 - resume auto-rescan if Live is still checked
        super().showEvent(e)
        if self._live.isChecked() and not self._timer.isActive():
            self._timer.start(self._live_secs.value() * 1000)

    # --- export ---

    def _export_csv(self) -> None:
        """Write the current results table to a CSV file."""
        if self._table.rowCount() == 0:
            self._status.setText("Nothing to export — scan first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export screener results", "screen.csv",
                                                        "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(_COLS)
                for r in range(self._table.rowCount()):
                    w.writerow([self._table.item(r, c).text() if self._table.item(r, c) else ""
                                for c in range(len(_COLS))])
            self._status.setText(f"Exported → {path}")
        except OSError as exc:
            self._status.setText(f"Export failed: {exc}")
