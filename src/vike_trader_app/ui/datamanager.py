"""Data Manager space — a TradeStation/NinjaTrader-style window over the local cache.

Lists every cached ``(symbol, interval)`` series (bars, date range, on-disk size, pinned?) and
drives the data engine: Download/Extend (``cache.get_bars``), Repair gaps (``cache.repair_gaps``),
Pin/Unpin a precomputed rollup (``data.rollup``), Delete (``parquet_source.delete_series``).

Self-contained like ScreenerTab/JournalTab; ``root``/``pins_path`` are injectable for tests.
All data access runs on the main thread (the Parquet reader isn't thread-safe).
"""

import time

from PySide6 import QtCore, QtWidgets

from ..data.binance_source import interval_ms
from ..data.cache import DEFAULT_ROOT, get_bars, repair_gaps
from ..data.instruments import ensure_presets, profile_for_symbol, spec_for_symbol
from ..data.parquet_source import delete_series
from ..data.rollup import load_pins, refresh_rollup, save_pins
from ..data.sources import select_source
from . import theme
from .datamanager_data import (
    instrument_detail,
    instrument_label,
    quality_summary,
    row_cells,
    series_size_bytes,
)

_PINS_PATH = "storage/pins.json"
_COLS = ["Symbol", "Timeframe", "Bars", "From", "To", "Size", "📌", "Instrument"]
_DAY_MS = 86_400_000
_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]


class DataManagerTab(QtWidgets.QWidget):
    """Catalog table + toolbar over the local data cache."""

    def __init__(self, root: str | None = None, pins_path: str | None = None, parent=None):
        super().__init__(parent)
        self._root = root or DEFAULT_ROOT
        self._pins_path = pins_path or _PINS_PATH
        self._cat = None  # built lazily on first refresh — don't read the catalog at app startup
        self._presets_ready = False  # broker-profile presets seeded lazily on first refresh

        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        self.btn_refresh = QtWidgets.QPushButton("↻ Refresh")
        self.btn_update_all = QtWidgets.QPushButton("⟳ Update all")
        self.btn_download = QtWidgets.QPushButton("⤓ Download / Extend…")
        self.btn_inspect = QtWidgets.QPushButton("🔍 Inspect")
        self.btn_repair = QtWidgets.QPushButton("🩹 Repair gaps")
        self.btn_pin = QtWidgets.QPushButton("📌 Pin / Unpin")
        self.btn_delete = QtWidgets.QPushButton("🗑 Delete")
        self.btn_update_all.setToolTip("Fetch up-to-now for every cached series")
        self.btn_inspect.setToolTip("Check the selected series for gaps / OHLC anomalies")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_update_all.clicked.connect(self._on_update_all)
        self.btn_download.clicked.connect(self._on_download)
        self.btn_inspect.clicked.connect(self._on_inspect)
        self.btn_repair.clicked.connect(self._on_repair)
        self.btn_pin.clicked.connect(self._on_pin)
        self.btn_delete.clicked.connect(self._on_delete)
        for b in (self.btn_refresh, self.btn_update_all, self.btn_download, self.btn_inspect,
                  self.btn_repair, self.btn_pin, self.btn_delete):
            bar.addWidget(b)
        bar.addStretch(1)
        self.count_label = QtWidgets.QLabel("")
        self.count_label.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        bar.addWidget(self.count_label)
        root_layout.addLayout(bar)

        self._table = QtWidgets.QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self._table.doubleClicked.connect(lambda *_: self._on_inspect())
        root_layout.addWidget(self._table, 1)

        # operation log (downloads / repairs / inspect reports) — read-only, like QDM's log
        log_header = QtWidgets.QLabel("ACTIVITY LOG")
        log_header.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;padding:4px 2px 0;"
        )
        root_layout.addWidget(log_header)
        self._log_view = QtWidgets.QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(96)   # always visible, never squeezed to nothing
        self._log_view.setMaximumHeight(150)
        self._log_view.setPlaceholderText("Downloads, repairs and inspect reports appear here…")
        self._log_view.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.PANEL2};color:{theme.TEXT2};border:1px solid "
            f"{theme.BORDER};border-radius:6px;font-family:{theme.FONT_MONO};font-size:11px;}}"
        )
        root_layout.addWidget(self._log_view)
        # No refresh() here: the table populates lazily on first show (showEvent), so app startup
        # never reads the catalog for a tab the user may not open.

    def _log(self, msg: str) -> None:
        """Append a timestamped line to the operation log."""
        self._log_view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")

    def _make_catalog(self):
        """Prefer the DuckDB catalog — it answers count/min/max from Parquet *statistics* without
        reading the bars (≈50× faster first open). Falls back to the Polars Catalog (which reads
        each series in full) only when the optional ``[duck]`` extra isn't installed.
        """
        try:
            from ..data.duck_catalog import DuckCatalog

            return DuckCatalog(self._root)
        except ImportError:
            from ..data.catalog import Catalog

            return Catalog(self._root)

    def _catalog(self):
        if self._cat is None:
            self._cat = self._make_catalog()
        return self._cat

    # --- table ---
    def refresh(self) -> None:
        """Repopulate the table from the catalog (+ pin state + on-disk size + instrument spec)."""
        if not self._presets_ready:  # seed the 5 broker-profile presets once (idempotent)
            try:
                ensure_presets(self._root)
            except Exception:  # noqa: BLE001 - a read-only/locked storage dir just defers presets
                pass
            self._presets_ready = True
        pins = {tuple(p) for p in load_pins(self._pins_path)}
        datasets = self._catalog().list_datasets()
        self._table.setRowCount(len(datasets))
        for r, info in enumerate(datasets):
            pinned = (info.symbol, info.interval) in pins
            size = series_size_bytes(self._root, info.symbol, info.interval)
            for c, val in enumerate(row_cells(info, pinned, size)):
                item = QtWidgets.QTableWidgetItem(val)
                if c >= 2:  # numeric / date / size / pin columns read right
                    item.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
                self._table.setItem(r, c, item)
            spec = spec_for_symbol(info.symbol, self._root)  # self-describing: tick/decimals
            cell = QtWidgets.QTableWidgetItem(instrument_label(spec))
            cell.setForeground(QtWidgets.QApplication.palette().mid())
            self._table.setItem(r, len(_COLS) - 1, cell)
        self.count_label.setText(f"{len(datasets)} series")

    def _selected(self) -> tuple[str, str] | None:
        """The selected row's ``(symbol, interval)``, or None."""
        row = self._table.currentRow()
        if row < 0 or self._table.item(row, 0) is None:
            return None
        return self._table.item(row, 0).text(), self._table.item(row, 1).text()

    # --- actions (data engine) ---
    def _on_pin(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        pins = {tuple(p) for p in load_pins(self._pins_path)}
        if sel in pins:
            pins.discard(sel)
        else:
            pins.add(sel)
            try:
                refresh_rollup(self._root, sel[0], sel[1])  # materialise it now
            except Exception:  # noqa: BLE001 - a transient read/write just defers the rollup
                pass
        save_pins(self._pins_path, list(pins))
        self.refresh()
        self._log(f"{'Pinned' if sel in pins else 'Unpinned'} {sel[0]} {sel[1]}")

    def _on_repair(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        symbol, interval = sel
        now = int(time.time() * 1000)
        self._log(f"Repairing gaps in {symbol} {interval}…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            n = repair_gaps(symbol, interval, 0, now, root=self._root,
                            fetcher=select_source(symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - report, no crash
            QtWidgets.QApplication.restoreOverrideCursor()
            self._log(f"Repair {symbol} {interval} failed: {exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.refresh()
        self._log(f"Repaired {symbol} {interval}: +{n} bar(s)")

    def _on_inspect(self) -> None:
        """Check the selected series for gaps / OHLC anomalies and report to the log."""
        sel = self._selected()
        if sel is None:
            return
        symbol, interval = sel
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            bars = self._catalog().query(symbol, interval)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._log(f"Inspect {symbol} {interval}: {quality_summary(bars, interval_ms(interval))}")
        spec = spec_for_symbol(symbol, self._root)
        self._log(f"  instrument: {instrument_detail(spec, profile_for_symbol(symbol))}")

    def _on_update_all(self) -> None:
        """Fetch up-to-now for every cached series (TradeStation/NinjaTrader 'Update all')."""
        datasets = self._catalog().list_datasets()
        now = int(time.time() * 1000)
        self._log(f"Update all: {len(datasets)} series…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for info in datasets:
                try:
                    before = info.n_bars
                    bars = get_bars(info.symbol, info.interval, info.start_ts, now,
                                    root=self._root,
                                    fetcher=select_source(info.symbol).fetch_bars_range)
                    self._log(f"  {info.symbol} {info.interval}: +{max(0, len(bars) - before)} bar(s)")
                except Exception as exc:  # noqa: BLE001 - skip a failing symbol, keep going
                    self._log(f"  {info.symbol} {info.interval}: failed — {exc}")
                QtWidgets.QApplication.processEvents()  # keep the log/UI live during the run
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.refresh()
        self._log("Update all: done")

    def _on_download(self) -> None:
        sel = self._selected()
        dlg = _DownloadDialog(self, default=sel)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        symbol, interval, days = dlg.values()
        if not symbol:
            return
        now = int(time.time() * 1000)
        self._log(f"Downloading {symbol} {interval} ({days}d)…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            get_bars(symbol, interval, now - days * _DAY_MS, now, root=self._root,
                     fetcher=select_source(symbol).fetch_bars_range)
        except Exception as exc:  # noqa: BLE001 - report, no crash
            QtWidgets.QApplication.restoreOverrideCursor()
            self._log(f"Download {symbol} {interval} failed: {exc}")
            return
        QtWidgets.QApplication.restoreOverrideCursor()
        self.refresh()
        self._log(f"Downloaded {symbol} {interval}")

    def _delete(self, symbol: str, interval: str) -> None:
        """Remove a series' files (no prompt — the prompt lives in ``_on_delete``, so tests skip it)."""
        delete_series(self._root, symbol, interval)
        # a deleted series shouldn't stay pinned
        pins = [p for p in load_pins(self._pins_path) if tuple(p) != (symbol, interval)]
        save_pins(self._pins_path, pins)
        self.refresh()
        self._log(f"Deleted {symbol} {interval}")

    def _on_delete(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        symbol, interval = sel
        ok = QtWidgets.QMessageBox.question(
            self, "Delete data",
            f"Delete all cached {symbol} {interval} data? This can't be undone.",
        )
        if ok == QtWidgets.QMessageBox.Yes:
            self._delete(symbol, interval)

    def showEvent(self, event):  # noqa: N802 - Qt override: refresh when the space is opened
        super().showEvent(event)
        self.refresh()
        if not self._log_view.toPlainText():  # one-time greeting so the log reads as a live panel
            self._log(f"Ready — {self._table.rowCount()} cached series. "
                      f"Select a row and Inspect, or Update all to refresh.")


class _DownloadDialog(QtWidgets.QDialog):
    """Pick a symbol/interval + how much history to fetch (prefilled from the selected row)."""

    def __init__(self, parent=None, default: tuple[str, str] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Download / Extend data")
        form = QtWidgets.QFormLayout(self)
        self._symbol = QtWidgets.QLineEdit(default[0] if default else "")
        self._symbol.setPlaceholderText("e.g. BTCUSDT or EURUSD")
        self._interval = QtWidgets.QComboBox()
        self._interval.addItems(_INTERVALS)
        if default:
            self._interval.setCurrentText(default[1])
        self._days = QtWidgets.QSpinBox()
        self._days.setRange(1, 36500)
        self._days.setValue(30)
        form.addRow("Symbol", self._symbol)
        form.addRow("Timeframe", self._interval)
        form.addRow("Days back", self._days)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, int]:
        return self._symbol.text().strip(), self._interval.currentText(), self._days.value()
