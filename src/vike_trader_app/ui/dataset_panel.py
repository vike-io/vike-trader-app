"""DataSet Symbols panel (right pane, 'Symbols' sub-tab).

Edit a DataSet's symbol list / linked provider / interval, request a backtest of a single
symbol or the whole DataSet, and ask the AI to suggest symbols. Public ops are dialog-free
for testability.
"""

from PySide6 import QtCore, QtWidgets

from ..data.datasets import DataSet, DateRange, load_dataset, parse_symbols, preset_datasets, save_dataset
from ..data.membership import parse_membership_csv

_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
_PROVIDERS = ["Auto", "binance", "bybit", "okx", "coinbase", "kraken", "yahoo", "dukascopy"]


class DataSetPanel(QtWidgets.QWidget):
    """Symbol editor + Test buttons for the selected DataSet."""

    test_symbol_requested = QtCore.Signal(str, str)   # (symbol, interval)
    test_dataset_requested = QtCore.Signal(object)    # DataSet

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        self._name = ""
        # Current in-memory membership ranges (preserved across save)
        self._ranges: dict[str, list[DateRange]] = {}
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self._provider = QtWidgets.QComboBox()
        self._provider.addItems(_PROVIDERS)
        self._interval = QtWidgets.QComboBox()
        self._interval.addItems(_INTERVALS)
        self._benchmark = QtWidgets.QLineEdit()
        self._benchmark.setPlaceholderText("optional, e.g. SPY / BTCUSDT (else equal-weight)")
        self._benchmark.setToolTip(
            "Benchmark symbol for the Studio Benchmark tab (alpha/beta/capture). "
            "Its cached bars are used; leave blank for an equal-weight buy-&-hold of the universe."
        )
        form.addRow("Provider", self._provider)
        form.addRow("Interval", self._interval)
        form.addRow("Benchmark", self._benchmark)
        layout.addLayout(form)

        self._symbols_list = QtWidgets.QListWidget()  # selectable rows for 'Test symbol'
        layout.addWidget(self._symbols_list, 1)
        layout.addWidget(QtWidgets.QLabel("Symbols (comma or newline separated)"))
        self._symbols = QtWidgets.QPlainTextEdit()
        self._symbols.setPlaceholderText("BTCUSDT, ETHUSDT, SOLUSDT…")
        self._symbols.textChanged.connect(self._sync_list)
        layout.addWidget(self._symbols, 1)

        ai_box = QtWidgets.QGroupBox("Ask the AI")
        ai_layout = QtWidgets.QVBoxLayout(ai_box)
        self._ai_query = QtWidgets.QLineEdit()
        self._ai_query.setPlaceholderText("e.g. top 10 liquid crypto majors")
        self.btn_ai = QtWidgets.QPushButton("Suggest")
        self.btn_ai.clicked.connect(self._on_ask_ai)
        self._ai_status = QtWidgets.QLabel("")
        ai_layout.addWidget(self._ai_query)
        ai_layout.addWidget(self.btn_ai)
        ai_layout.addWidget(self._ai_status)
        layout.addWidget(ai_box)

        # ------------------------------------------------------------------
        # Membership section (dynamic DataSet windows — read-only display)
        # ------------------------------------------------------------------
        membership_box = QtWidgets.QGroupBox("Membership (dynamic DataSet)")
        membership_layout = QtWidgets.QVBoxLayout(membership_box)
        self._membership_view = QtWidgets.QPlainTextEdit()
        self._membership_view.setReadOnly(True)
        self._membership_view.setPlaceholderText("No membership windows — static DataSet.")
        self._membership_view.setMaximumHeight(100)
        membership_layout.addWidget(self._membership_view)
        self.btn_import_membership = QtWidgets.QPushButton("⤒ Import membership…")
        self.btn_import_membership.clicked.connect(self._on_import_membership)
        membership_layout.addWidget(self.btn_import_membership)
        layout.addWidget(membership_box)

        bar = QtWidgets.QHBoxLayout()
        self.btn_save = QtWidgets.QPushButton("💾 Save")
        self.btn_test_symbol = QtWidgets.QPushButton("▶ Test symbol")
        self.btn_test_dataset = QtWidgets.QPushButton("▶ Test DataSet")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_test_symbol.clicked.connect(self._on_test_symbol)
        self.btn_test_dataset.clicked.connect(self._on_test_dataset)
        for b in (self.btn_save, self.btn_test_symbol, self.btn_test_dataset):
            bar.addWidget(b)
        bar.addStretch(1)
        layout.addLayout(bar)

    def _dataset(self, name: str) -> DataSet:
        return load_dataset(name, self._root) or preset_datasets().get(name) or DataSet(name)

    def load_dataset(self, name: str) -> None:
        self._name = name
        d = self._dataset(name)
        self._symbols.setPlainText("\n".join(d.symbols))
        self._provider.setCurrentText(d.provider or "Auto")
        self._interval.setCurrentText(d.interval)
        self._benchmark.setText(getattr(d, "benchmark", "") or "")
        # Populate membership from the loaded dataset's ranges
        self._ranges = dict(d.ranges)
        self._refresh_membership_view()

    def _sync_list(self) -> None:
        self._symbols_list.clear()
        self._symbols_list.addItems(parse_symbols(self._symbols.toPlainText()))

    def current_dataset(self) -> DataSet:
        choice = self._provider.currentText()
        return DataSet(
            name=self._name,
            symbols=parse_symbols(self._symbols.toPlainText()),
            provider=None if choice == "Auto" else choice,
            interval=self._interval.currentText(),
            ranges=dict(self._ranges),  # preserve current membership
            benchmark=self._benchmark.text().strip(),
        )

    def save(self) -> DataSet:
        d = self.current_dataset()
        save_dataset(d, self._root)
        return d

    def _on_save(self) -> None:
        self.save()

    def _on_test_symbol(self) -> None:
        item = self._symbols_list.currentItem()
        if item is not None:
            self.test_symbol_requested.emit(item.text(), self._interval.currentText())

    def _on_test_dataset(self) -> None:
        self.test_dataset_requested.emit(self.save())

    def apply_ai_suggestion(self, reply: str) -> None:
        """Parse an AI reply and append new symbols to the editor (deduped, order-preserving)."""
        existing = parse_symbols(self._symbols.toPlainText())
        merged = existing + [s for s in parse_symbols(reply) if s not in existing]
        self._symbols.setPlainText("\n".join(merged))

    def _on_ask_ai(self) -> None:
        from ..data.datasets import provider_group
        from ..ai.symbol_suggest import suggest_symbols

        query = self._ai_query.text().strip()
        if not query:
            return
        self._ai_status.setText("Asking…")
        self.btn_ai.setEnabled(False)  # the call is synchronous — block re-entrant clicks
        QtWidgets.QApplication.processEvents()
        try:
            symbols = suggest_symbols(query, group=provider_group(self.current_dataset()))
        except Exception as exc:  # noqa: BLE001 - missing [ai] extra / key / network: show, don't crash
            self._ai_status.setText(f"AI unavailable: {exc}")
            return
        finally:
            self.btn_ai.setEnabled(True)
        self.apply_ai_suggestion(" ".join(symbols))
        self._ai_status.setText(f"added {len(symbols)} symbol(s)")

    # ------------------------------------------------------------------
    # Membership public API (dialog-free, testable)
    # ------------------------------------------------------------------

    def membership_summary(self) -> str:
        """Return a human-readable string of the current membership windows.

        One line per symbol: ``SYM: start … end`` (or ``open`` for open-ended windows).
        Returns an empty string when there are no membership windows.
        """
        if not self._ranges:
            return ""
        lines = []
        for sym, windows in sorted(self._ranges.items()):
            for w in windows:
                from datetime import datetime, timezone as _tz
                start_dt = datetime.fromtimestamp(w.start_ts / 1000, tz=_tz.utc).strftime("%Y-%m-%d")
                if w.end_ts is not None:
                    end_dt = datetime.fromtimestamp(w.end_ts / 1000, tz=_tz.utc).strftime("%Y-%m-%d")
                else:
                    end_dt = "open"
                lines.append(f"{sym}: {start_dt} … {end_dt}")
        return "\n".join(lines)

    def _refresh_membership_view(self) -> None:
        summary = self.membership_summary()
        self._membership_view.setPlainText(summary)

    def apply_membership(self, ranges: dict[str, list[DateRange]]) -> None:
        """Set in-memory dataset ranges, refresh the read-only display, and persist to disk.

        Merges the new ranges into the current dataset (keeps symbols/provider/interval).
        """
        self._ranges = dict(ranges)
        self._refresh_membership_view()
        # Persist: save the full dataset with updated ranges
        d = self.current_dataset()
        save_dataset(d, self._root)

    def import_membership_csv(self, text: str) -> dict[str, list[DateRange]]:
        """Parse ``text`` as a membership CSV, then apply and persist.

        Returns the parsed ranges dict.
        """
        ranges = parse_membership_csv(text)
        self.apply_membership(ranges)
        return ranges

    def _on_import_membership(self) -> None:
        """Button handler: open a file dialog, read the CSV, import it."""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import membership CSV", "", "CSV / Text (*.csv *.txt);;All files (*)"
        )
        if not path:
            return
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            QtWidgets.QMessageBox.warning(self, "Import failed", str(exc))
            return
        self.import_membership_csv(text)
