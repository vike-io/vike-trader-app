"""DataSet Symbols panel (right pane, 'Symbols' sub-tab).

Edit a DataSet's symbol list / linked provider / interval, and request a backtest of a single
symbol or the whole DataSet. The Ask-the-AI box is added in a later task. Public ops are
dialog-free for testability.
"""

from PySide6 import QtCore, QtWidgets

from ..data.datasets import DataSet, load_dataset, parse_symbols, preset_datasets, save_dataset

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
        layout = QtWidgets.QVBoxLayout(self)

        form = QtWidgets.QFormLayout()
        self._provider = QtWidgets.QComboBox()
        self._provider.addItems(_PROVIDERS)
        self._interval = QtWidgets.QComboBox()
        self._interval.addItems(_INTERVALS)
        form.addRow("Provider", self._provider)
        form.addRow("Interval", self._interval)
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
        QtWidgets.QApplication.processEvents()
        try:
            symbols = suggest_symbols(query, group=provider_group(self.current_dataset()))
        except Exception as exc:  # noqa: BLE001 - missing [ai] extra / key / network: show, don't crash
            self._ai_status.setText(f"AI unavailable: {exc}")
            return
        self.apply_ai_suggestion(" ".join(symbols))
        self._ai_status.setText(f"added {len(symbols)} symbol(s)")
