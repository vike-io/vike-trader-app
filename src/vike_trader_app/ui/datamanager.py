"""Data Manager space — a TradeStation/NinjaTrader-style window over the local cache.

Lists every cached ``(symbol, interval)`` series (bars, date range, on-disk size, pinned?) and
drives the data engine: Download/Extend (``cache.get_bars``), Repair gaps (``cache.repair_gaps``),
Pin/Unpin a precomputed rollup (``data.rollup``), Delete (``parquet_source.delete_series``).

Self-contained like ScreenerTab/JournalTab; ``root``/``pins_path`` are injectable for tests.
Writes run on the main thread; reads are thread-safe (per-call DuckDB).
"""

import time

from PySide6 import QtCore, QtWidgets

from ..data.binance_source import interval_ms
from ..data.cache import DEFAULT_ROOT, get_bars, repair_gaps
from ..data.csv_import import aggregate, infer_interval_ms, ms_to_interval, parse_csv
from ..data.instruments import ensure_presets, profile_for_symbol, spec_for_symbol
from ..data.parquet_source import append_series, delete_series, read_series
from ..data.rollup import load_pins, refresh_rollup, save_pins
from ..data.sources import CRYPTO_PROVIDERS, select_source
from . import theme
from .datamanager_data import (
    instrument_detail,
    instrument_label,
    quality_summary,
    row_cells,
    series_size_bytes,
    source_label,
)

_PINS_PATH = "storage/pins.json"
_COLS = ["Symbol", "Timeframe", "Bars", "From", "To", "Size", "📌", "Source", "Instrument"]
_DAY_MS = 86_400_000
_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
# explicit providers selectable in the Download / DataSet dialogs (besides "Auto")
PROVIDER_CHOICES = [*CRYPTO_PROVIDERS, "yahoo", "dukascopy"]


def config_root_for(data_root: str) -> str:
    """Where user-facing config (broker profiles, DataSets) lives, given the bar-data root.

    Profiles/DataSets are config humans edit — they belong *beside* the parquet cache, not inside
    it. So the default ``storage/parquet`` data root maps to ``storage``; any other root (e.g. a
    test tmp dir, not named ``parquet``) is used as-is.
    """
    from pathlib import Path

    p = Path(data_root)
    return str(p.parent) if p.name == "parquet" else str(p)


class DataManagerTab(QtWidgets.QWidget):
    """Catalog table + toolbar over the local data cache."""

    test_symbol_requested = QtCore.Signal(str, object)        # (symbol, bars)
    test_dataset_requested = QtCore.Signal(object, object)    # (DataSet, {symbol: bars})

    def __init__(self, root: str | None = None, pins_path: str | None = None,
                 config_root: str | None = None, parent=None):
        super().__init__(parent)
        self._root = root or DEFAULT_ROOT
        # profiles/DataSets live beside the parquet cache (storage/), not inside it
        self._config_root = config_root or config_root_for(self._root)
        self._pins_path = pins_path or _PINS_PATH
        self._cat = None  # built lazily on first refresh — don't read the catalog at app startup
        self._symbol_filter = None
        self._presets_ready = False  # broker-profile presets seeded lazily on first refresh

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        outer.addWidget(splitter, 1)

        from .dataset_tree import DataSetTree
        self.tree = DataSetTree(self._config_root)
        self.tree.dataset_selected.connect(self._on_dataset_selected)
        splitter.addWidget(self.tree)

        self.subtabs = QtWidgets.QTabWidget()
        splitter.addWidget(self.subtabs)
        splitter.setStretchFactor(1, 1)

        from .dataset_panel import DataSetPanel
        from .event_providers_panel import EventProvidersPanel
        from .providers_panel import ProvidersPanel
        from .streaming_providers_panel import StreamingProvidersPanel
        self.panel = DataSetPanel(self._config_root)
        self.providers = ProvidersPanel(self._config_root)
        self.event_providers = EventProvidersPanel(self._config_root)
        self.streaming_providers = StreamingProvidersPanel(self._config_root)
        self.panel.test_symbol_requested.connect(self._on_test_symbol_req)
        self.panel.test_dataset_requested.connect(self._on_test_dataset_req)

        cached = QtWidgets.QWidget()
        cached_layout = QtWidgets.QVBoxLayout(cached)
        cached_layout.setContentsMargins(0, 0, 0, 0)
        cached_layout.setSpacing(6)

        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        self.btn_refresh = QtWidgets.QPushButton("↻ Refresh")
        self.btn_update_all = QtWidgets.QPushButton("⟳ Update all")
        self.btn_download = QtWidgets.QPushButton("⤓ Download / Extend…")
        self.btn_import = QtWidgets.QPushButton("⤒ Import CSV…")
        self.btn_inspect = QtWidgets.QPushButton("🔍 Inspect")
        self.btn_repair = QtWidgets.QPushButton("🩹 Repair gaps")
        self.btn_clean = QtWidgets.QPushButton("🧼 Clean data")
        self.btn_pin = QtWidgets.QPushButton("📌 Pin / Unpin")
        self.btn_profiles = QtWidgets.QPushButton("⚙ Instruments…")
        self.btn_truncate = QtWidgets.QPushButton("✂ Truncate…")
        self.btn_remove_inactive = QtWidgets.QPushButton("🧹 Remove inactive…")
        self.btn_delete = QtWidgets.QPushButton("🗑 Delete")
        self.btn_update_all.setToolTip("Fetch up-to-now for every cached series")
        self.btn_import.setToolTip("Import an OHLCV CSV (auto-detect format; optional TZ shift + aggregate)")
        self.btn_inspect.setToolTip("Check the selected series for gaps / OHLC anomalies")
        self.btn_profiles.setToolTip("Edit broker profiles & instrument specs (tick / pip / step / size)")
        self.btn_truncate.setToolTip("Delete cached bars before/after a date")
        self.btn_clean.setToolTip("Repair zero/NaN/out-of-range OHLC + duplicate timestamps")
        self.btn_remove_inactive.setToolTip(
            "Delete cached series with no data (or last data before a date)")
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_update_all.clicked.connect(self._on_update_all)
        self.btn_download.clicked.connect(self._on_download)
        self.btn_import.clicked.connect(self._on_import_csv)
        self.btn_inspect.clicked.connect(self._on_inspect)
        self.btn_repair.clicked.connect(self._on_repair)
        self.btn_clean.clicked.connect(self._on_clean)
        self.btn_pin.clicked.connect(self._on_pin)
        self.btn_profiles.clicked.connect(self._on_profiles)
        self.btn_truncate.clicked.connect(self._on_truncate)
        self.btn_remove_inactive.clicked.connect(self._on_remove_inactive)
        self.btn_delete.clicked.connect(self._on_delete)
        for b in (self.btn_refresh, self.btn_update_all, self.btn_download, self.btn_import,
                  self.btn_inspect, self.btn_repair, self.btn_clean, self.btn_pin, self.btn_profiles,
                  self.btn_truncate, self.btn_remove_inactive, self.btn_delete):
            bar.addWidget(b)
        bar.addStretch(1)
        self.count_label = QtWidgets.QLabel("")
        self.count_label.setStyleSheet(f"color:{theme.TEXT3};font-size:11px;")
        bar.addWidget(self.count_label)
        cached_layout.addLayout(bar)

        self._table = QtWidgets.QTableWidget(0, len(_COLS))
        self._table.setHorizontalHeaderLabels(_COLS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self._table.doubleClicked.connect(lambda *_: self._on_inspect())
        cached_layout.addWidget(self._table, 1)

        log_header = QtWidgets.QLabel("ACTIVITY LOG")
        log_header.setStyleSheet(
            f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;padding:4px 2px 0;"
        )
        cached_layout.addWidget(log_header)
        self._log_view = QtWidgets.QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMinimumHeight(96)
        self._log_view.setMaximumHeight(150)
        self._log_view.setPlaceholderText("Downloads, repairs and inspect reports appear here…")
        self._log_view.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.PANEL2};color:{theme.TEXT2};border:1px solid "
            f"{theme.BORDER};border-radius:6px;font-family:{theme.FONT_MONO};font-size:11px;}}"
        )
        cached_layout.addWidget(self._log_view)

        self.subtabs.addTab(self.panel, "Symbols")
        self.subtabs.addTab(cached, "Cached Series")
        self.subtabs.addTab(self.providers, "Historical Providers")
        self.subtabs.addTab(self.event_providers, "Event Providers")
        self.subtabs.addTab(self.streaming_providers, "Streaming Providers")

        self.providers.testbed_result.connect(self._log)
        self.tree.reload()
        # No refresh() here: the table populates lazily on first show (showEvent), so app startup
        # never reads the catalog for a tab the user may not open.

    def _log(self, msg: str) -> None:
        """Append a timestamped line to the operation log."""
        self._log_view.appendPlainText(f"{time.strftime('%H:%M:%S')}  {msg}")

    def set_symbol_filter(self, symbols) -> None:
        """Restrict the Cached-Series table to ``symbols`` (None = show everything)."""
        self._symbol_filter = {s.upper() for s in symbols} if symbols else None

    def _on_dataset_selected(self, name: str) -> None:
        from ..data.datasets import load_dataset, preset_datasets
        self.panel.load_dataset(name)
        self.subtabs.setCurrentWidget(self.panel)
        d = load_dataset(name, self._config_root) or preset_datasets().get(name)
        self.set_symbol_filter(d.symbols if d else None)
        self.refresh()

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
                ensure_presets(self._config_root)
            except Exception:  # noqa: BLE001 - a read-only/locked storage dir just defers presets
                pass
            self._presets_ready = True
        pins = {tuple(p) for p in load_pins(self._pins_path)}
        datasets = self._catalog().list_datasets()
        if self._symbol_filter is not None:
            datasets = [i for i in datasets if i.symbol.upper() in self._symbol_filter]
        self._table.setRowCount(len(datasets))
        for r, info in enumerate(datasets):
            pinned = (info.symbol, info.interval) in pins
            size = series_size_bytes(self._root, info.symbol, info.interval)
            for c, val in enumerate(row_cells(info, pinned, size)):
                item = QtWidgets.QTableWidgetItem(val)
                if c >= 2:  # numeric / date / size / pin columns read right
                    item.setTextAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight)
                self._table.setItem(r, c, item)
            # Source: the datasource this symbol routes to (inferred — not stored per series)
            src = QtWidgets.QTableWidgetItem(source_label(info.symbol))
            src.setForeground(QtWidgets.QApplication.palette().mid())
            src.setToolTip("Auto-routed datasource, inferred from the symbol (not stored per series).\n"
                           "Forex: Dukascopy deep history + Yahoo recent edge · Crypto: Binance")
            self._table.setItem(r, _COLS.index("Source"), src)
            spec = spec_for_symbol(info.symbol, self._config_root)  # self-describing: tick/decimals
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

    def clean_series(self, symbol: str, interval: str) -> list:
        """Repair a cached series in place; returns the audit log (also logged). Rewrites only if changed."""
        from ..data.quality import repair_bars
        bars = read_series(self._root, symbol, interval)
        repaired, audit = repair_bars(bars, interval_ms(interval))
        if audit:
            delete_series(self._root, symbol, interval)
            append_series(repaired, self._root, symbol, interval)
            self.refresh()
        self._log(
            f"Clean {symbol} {interval}: {len(audit)} fix(es)"
            + ("" if not audit else "\n" + "\n".join("  • " + a for a in audit[:20]))
        )
        return audit

    def _on_clean(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        symbol, interval = sel
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.clean_series(symbol, interval)
        except Exception as exc:  # noqa: BLE001 - report, no crash
            self._log(f"Clean {symbol} {interval} failed: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

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
        spec = spec_for_symbol(symbol, self._config_root)
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

    def download_series(self, symbol: str, interval: str, days: int,
                        provider: str | None = None) -> int:
        """Fetch ``days`` of history for one symbol from ``provider`` (None = Auto). No dialog.

        Returns the number of bars now cached for the window. The prompt-free path the Download
        dialog and tests call.
        """
        from ..data.provider_chain import fetch_for
        now = int(time.time() * 1000)

        def fetcher(sym, iv, start, end, progress=None):
            bars, _used = fetch_for(sym, iv, start, end, root=self._config_root,
                                    linked_provider=provider or None, progress=progress)
            return bars

        bars = get_bars(symbol, interval, now - days * _DAY_MS, now, root=self._root, fetcher=fetcher)
        self.refresh()
        self._log(f"Downloaded {symbol} {interval} ({days}d) via provider chain")
        return len(bars)

    def _on_download(self) -> None:
        sel = self._selected()
        dlg = _DownloadDialog(self, default=sel)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        symbol, interval, days, provider = dlg.values()
        if not symbol:
            return
        self._log(f"Downloading {symbol} {interval} ({days}d)…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.download_series(symbol, interval, days, provider)
        except Exception as exc:  # noqa: BLE001 - report, no crash
            self._log(f"Download {symbol} {interval} failed: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def import_csv_file(self, path: str, symbol: str, tz_offset_minutes: int = 0,
                        target_interval: str | None = None) -> int:
        """Parse a CSV at ``path`` into the cache for ``symbol`` — returns bars written. No dialog.

        Interval is the one chosen (``target_interval``, which also triggers aggregation) or the
        one inferred from row spacing. The prompt-free path the Import dialog calls (and tests use).
        """
        from pathlib import Path

        text = Path(path).read_text(encoding="utf-8", errors="replace")
        bars = parse_csv(text, tz_offset_minutes=tz_offset_minutes)
        if not bars:
            self._log(f"Import {symbol}: no rows parsed from {path}")
            return 0
        interval = target_interval or ms_to_interval(infer_interval_ms(bars)) or "1m"
        if target_interval:
            bars = aggregate(bars, target_interval)
        append_series(bars, self._root, symbol.upper(), interval)
        self.refresh()
        self._log(f"Imported {symbol.upper()} {interval}: {len(bars):,} bar(s) from CSV")
        return len(bars)

    def _on_import_csv(self) -> None:
        sel = self._selected()
        dlg = _ImportCsvDialog(self, default_symbol=sel[0] if sel else "")
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        path, symbol, tz_off, target = dlg.values()
        if not path or not symbol:
            return
        self._log(f"Importing {symbol} from {path}…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.import_csv_file(path, symbol, tz_off, target)
        except Exception as exc:  # noqa: BLE001 - report, no crash
            self._log(f"Import {symbol} failed: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def _on_profiles(self) -> None:
        """Open the broker-profile / instrument editor; refresh after (specs may have changed)."""
        from .profile_editor import ProfileEditorDialog

        ProfileEditorDialog(self._config_root, self).exec()
        self.refresh()

    def download_dataset(self, dataset, days: int) -> int:
        """Fetch ``days`` of history for every symbol in a DataSet (its provider, or Auto each).

        Returns how many symbols were fetched without error. Used by the DataSets dialog.
        """
        ok = 0
        self._log(f"DataSet '{dataset.name}': {len(dataset.symbols)} symbols, {days}d…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            for sym in dataset.symbols:
                try:
                    self.download_series(sym, dataset.interval, days, dataset.provider)
                    ok += 1
                except Exception as exc:  # noqa: BLE001 - skip a failing symbol, keep going
                    self._log(f"  {sym}: failed — {exc}")
                QtWidgets.QApplication.processEvents()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._log(f"DataSet '{dataset.name}': done ({ok}/{len(dataset.symbols)})")
        return ok

    def _load_symbol_bars(self, symbol: str, interval: str, days: int = 365) -> list:
        """Load up to ``days`` of history for one symbol (cached), routing via the provider chain."""
        from ..data.provider_chain import fetch_for
        now = int(time.time() * 1000)

        def fetcher(sym, iv, start, end, progress=None):
            bars, _ = fetch_for(sym, iv, start, end, root=self._config_root, progress=progress)
            return bars

        return get_bars(symbol, interval, now - days * _DAY_MS, now, root=self._root, fetcher=fetcher)

    def _on_test_symbol_req(self, symbol: str, interval: str) -> None:
        self._log(f"Loading {symbol} {interval} for single-symbol test…")
        try:
            bars = self._load_symbol_bars(symbol, interval)
        except Exception as exc:  # noqa: BLE001 - report, no crash
            self._log(f"Test symbol {symbol} failed: {exc}")
            return
        self.test_symbol_requested.emit(symbol, bars)

    def _on_test_dataset_req(self, dataset) -> None:
        self._log(f"Loading {len(dataset.symbols)} symbols for portfolio test '{dataset.name}'…")
        import os
        from concurrent.futures import ThreadPoolExecutor

        def _load_one(sym):
            # Runs on a worker thread: reads (per-call DuckDB) + fetches (network) + appends
            # (per-(symbol,interval) lock) are all thread-safe across different symbols.
            # NO Qt / _log calls here — return the outcome for main-thread logging.
            try:
                return sym, self._load_symbol_bars(sym, dataset.interval), None
            except Exception as exc:  # noqa: BLE001 - isolate a failing symbol
                return sym, None, str(exc)

        workers = min(8, (os.cpu_count() or 4))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_load_one, list(dataset.symbols)))

        bars_by_symbol = {}
        for sym, bars, err in results:          # main thread: log + assemble
            if err is not None:
                self._log(f"  {sym}: failed — {err}")
            elif bars:
                bars_by_symbol[sym] = bars
        self.test_dataset_requested.emit(dataset, bars_by_symbol)

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

    def truncate_series(self, symbol: str, interval: str, *, before_ms: int | None = None,
                        after_ms: int | None = None) -> int:
        """Delete cached bars before/after the given epoch-ms bound(s); returns bars removed (no dialog)."""
        from ..data.parquet_source import truncate_series as _truncate
        n = _truncate(self._root, symbol, interval, before_ts=before_ms, after_ts=after_ms)
        self.refresh()
        self._log(f"Truncated {symbol} {interval}: removed {n:,} bar(s)")
        return n

    def _on_truncate(self) -> None:
        sel = self._selected()
        if sel is None:
            return
        symbol, interval = sel
        dlg = _TruncateDialog(self, default=sel)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        before_ms, after_ms = dlg.values()   # one is set, the other None
        ok = QtWidgets.QMessageBox.question(
            self, "Truncate data",
            f"Delete {symbol} {interval} bars "
            f"{'before' if before_ms is not None else 'after'} the chosen date? This can't be undone.")
        if ok == QtWidgets.QMessageBox.Yes:
            self.truncate_series(symbol, interval, before_ms=before_ms, after_ms=after_ms)

    def remove_inactive(self, *, zero_bars: bool = True, last_before_ms: int | None = None) -> list:
        """Delete dead/stale cached series (no dialog); returns the removed ``(symbol, interval)`` list."""
        from .datamanager_data import inactive_candidates
        cand = inactive_candidates(self._catalog().list_datasets(), zero_bars=zero_bars,
                                   last_before_ms=last_before_ms)
        for symbol, interval in cand:
            self._delete(symbol, interval)
        self._log(f"Removed {len(cand)} inactive series")
        return cand

    def _on_remove_inactive(self) -> None:
        dlg = _RemoveInactiveDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        zero_bars, last_before_ms = dlg.values()
        from .datamanager_data import inactive_candidates
        cand = inactive_candidates(self._catalog().list_datasets(), zero_bars=zero_bars,
                                   last_before_ms=last_before_ms)
        if not cand:
            self._log("Remove inactive: nothing to prune")
            return
        ok = QtWidgets.QMessageBox.question(
            self, "Remove inactive",
            f"Delete {len(cand)} inactive series? This can't be undone:\n"
            + ", ".join(f"{s} {i}" for s, i in cand[:20]) + ("…" if len(cand) > 20 else ""))
        if ok == QtWidgets.QMessageBox.Yes:
            self.remove_inactive(zero_bars=zero_bars, last_before_ms=last_before_ms)

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
        self._provider = QtWidgets.QComboBox()
        self._provider.addItems(["Auto", *PROVIDER_CHOICES])
        self._provider.setToolTip("Auto routes by symbol; or force a specific exchange/provider")
        form.addRow("Symbol", self._symbol)
        form.addRow("Timeframe", self._interval)
        form.addRow("Provider", self._provider)
        form.addRow("Days back", self._days)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[str, str, int, str | None]:
        choice = self._provider.currentText()
        provider = None if choice == "Auto" else choice
        return self._symbol.text().strip(), self._interval.currentText(), self._days.value(), provider


class _ImportCsvDialog(QtWidgets.QDialog):
    """Pick a CSV + symbol + source-timezone offset + (optional) aggregate target."""

    def __init__(self, parent=None, default_symbol: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Import CSV data")
        form = QtWidgets.QFormLayout(self)

        self._path = QtWidgets.QLineEdit()
        self._path.setPlaceholderText("path to an OHLCV .csv")
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row = QtWidgets.QHBoxLayout()
        path_row.addWidget(self._path, 1)
        path_row.addWidget(browse)
        path_wrap = QtWidgets.QWidget()
        path_wrap.setLayout(path_row)

        self._symbol = QtWidgets.QLineEdit(default_symbol)
        self._symbol.setPlaceholderText("e.g. EURUSD")
        self._tz = QtWidgets.QSpinBox()
        self._tz.setRange(-720, 840)
        self._tz.setSingleStep(30)
        self._tz.setSuffix(" min from UTC")
        self._tz.setToolTip("Source data's offset from UTC (e.g. 120 for UTC+2). Stamps are shifted to UTC.")
        self._interval = QtWidgets.QComboBox()
        self._interval.addItems(["as-is (detected)", *_INTERVALS])
        self._interval.setToolTip("Keep the file's resolution, or aggregate up to a higher timeframe")

        form.addRow("CSV file", path_wrap)
        form.addRow("Symbol", self._symbol)
        form.addRow("Source TZ", self._tz)
        form.addRow("Aggregate to", self._interval)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _browse(self) -> None:
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose CSV", "", "CSV / text (*.csv *.txt);;All files (*)"
        )
        if fn:
            self._path.setText(fn)

    def values(self) -> tuple[str, str, int, str | None]:
        choice = self._interval.currentText()
        target = None if choice.startswith("as-is") else choice
        return self._path.text().strip(), self._symbol.text().strip(), self._tz.value(), target


class _TruncateDialog(QtWidgets.QDialog):
    """Choose a date and a direction (before / after) to delete cached bars for a series."""

    def __init__(self, parent=None, default: tuple[str, str] | None = None):
        super().__init__(parent)
        symbol, interval = default if default else ("", "")
        self.setWindowTitle(f"Truncate {symbol} {interval}" if symbol else "Truncate data")
        form = QtWidgets.QFormLayout(self)

        self._date = QtWidgets.QDateEdit()
        self._date.setCalendarPopup(True)
        self._date.setDate(QtCore.QDate.currentDate())

        self._before = QtWidgets.QRadioButton("Delete before date (keep ≥ chosen date)")
        self._after = QtWidgets.QRadioButton("Delete after date (keep ≤ chosen date)")
        self._before.setChecked(True)

        form.addRow("Date", self._date)
        form.addRow(self._before)
        form.addRow(self._after)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> tuple[int | None, int | None]:
        """Return ``(before_ms, after_ms)`` with exactly one set based on the chosen radio."""
        date = self._date.date()
        ms = QtCore.QDateTime(date, QtCore.QTime(0, 0), QtCore.Qt.UTC).toMSecsSinceEpoch()
        if self._before.isChecked():
            return ms, None    # delete bars before this date
        return None, ms        # delete bars after this date


class _RemoveInactiveDialog(QtWidgets.QDialog):
    """Options for removing inactive (dead / stale) cached series."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Remove inactive series")
        form = QtWidgets.QFormLayout(self)

        self._zero_bars = QtWidgets.QCheckBox("Remove series with no data (0 bars)")
        self._zero_bars.setChecked(True)

        self._stale_check = QtWidgets.QCheckBox("Also remove series with last data before:")
        self._stale_date = QtWidgets.QDateEdit()
        self._stale_date.setCalendarPopup(True)
        self._stale_date.setDate(QtCore.QDate.currentDate())
        self._stale_date.setEnabled(False)
        self._stale_check.toggled.connect(self._stale_date.setEnabled)

        self._preview = QtWidgets.QLabel("Select options to preview candidates.")

        # update the preview when options change
        self._zero_bars.toggled.connect(self._update_preview)
        self._stale_check.toggled.connect(self._update_preview)
        self._stale_date.dateChanged.connect(self._update_preview)

        stale_row = QtWidgets.QHBoxLayout()
        stale_row.addWidget(self._stale_check)
        stale_row.addWidget(self._stale_date)
        stale_wrap = QtWidgets.QWidget()
        stale_wrap.setLayout(stale_row)

        form.addRow(self._zero_bars)
        form.addRow(stale_wrap)
        form.addRow("Preview:", self._preview)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _update_preview(self) -> None:
        """Refresh the candidate count label based on current options (if catalog is reachable)."""
        # The dialog has no catalog reference — show a static hint instead.
        parts = []
        if self._zero_bars.isChecked():
            parts.append("0-bar series")
        if self._stale_check.isChecked():
            parts.append(f"stale before {self._stale_date.date().toString('yyyy-MM-dd')}")
        self._preview.setText("Will prune: " + " + ".join(parts) if parts else "Nothing selected.")

    def values(self) -> tuple[bool, int | None]:
        """Return ``(zero_bars, last_before_ms)``; ``last_before_ms`` is None unless the stale checkbox is on."""
        zero_bars = self._zero_bars.isChecked()
        last_before_ms = None
        if self._stale_check.isChecked():
            date = self._stale_date.date()
            last_before_ms = QtCore.QDateTime(date, QtCore.QTime(0, 0),
                                              QtCore.Qt.UTC).toMSecsSinceEpoch()
        return zero_bars, last_before_ms
