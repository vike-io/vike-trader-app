"""DataSets manager — create/edit named symbol collections and download them in bulk.

Wealth-Lab's DataSet concept: a named list of symbols with a default provider + interval. The
'Download all' button hands the DataSet to a callback (the Data Manager's ``download_dataset``)
so the actual fetching/logging stays in one place. Public ops are dialog-free for testability.
"""

from PySide6 import QtWidgets

from ..data.datasets import (
    DataSet,
    delete_dataset,
    ensure_examples,
    list_datasets,
    load_dataset,
    parse_symbols,
    preset_datasets,
    save_dataset,
)

_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]
_PROVIDERS = ["Auto", "binance", "bybit", "okx", "coinbase", "kraken", "yahoo", "dukascopy"]


class DataSetEditorDialog(QtWidgets.QDialog):
    """Manage DataSets; 'Download all' calls ``on_download(dataset, days)`` if provided."""

    def __init__(self, root: str, on_download=None, parent=None):
        super().__init__(parent)
        self._root = root
        self._on_download = on_download
        try:
            ensure_examples(root)
        except Exception:  # noqa: BLE001 - read-only storage just means no examples seeded
            pass
        self.setWindowTitle("DataSets")
        self.resize(520, 460)
        layout = QtWidgets.QVBoxLayout(self)

        top = QtWidgets.QFormLayout()
        self._combo = QtWidgets.QComboBox()
        self._combo.addItems(self._names())
        self._combo.currentTextChanged.connect(self._load)
        self._provider = QtWidgets.QComboBox()
        self._provider.addItems(_PROVIDERS)
        self._interval = QtWidgets.QComboBox()
        self._interval.addItems(_INTERVALS)
        self._days = QtWidgets.QSpinBox()
        self._days.setRange(1, 36500)
        self._days.setValue(30)
        top.addRow("DataSet", self._combo)
        top.addRow("Provider", self._provider)
        top.addRow("Interval", self._interval)
        top.addRow("Days back", self._days)
        layout.addLayout(top)

        layout.addWidget(QtWidgets.QLabel("Symbols (comma or newline separated)"))
        self._symbols = QtWidgets.QPlainTextEdit()
        self._symbols.setPlaceholderText("BTCUSDT, ETHUSDT, SOLUSDT…")
        layout.addWidget(self._symbols, 1)

        bar = QtWidgets.QHBoxLayout()
        self.btn_new = QtWidgets.QPushButton("＋ New")
        self.btn_save = QtWidgets.QPushButton("💾 Save")
        self.btn_delete = QtWidgets.QPushButton("🗑 Delete")
        self.btn_download = QtWidgets.QPushButton("⤓ Download all")
        self.btn_new.clicked.connect(self._on_new)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_delete.clicked.connect(self._on_delete)
        self.btn_download.clicked.connect(self._on_download_all)
        for b in (self.btn_new, self.btn_save, self.btn_delete):
            bar.addWidget(b)
        bar.addStretch(1)
        self._status = QtWidgets.QLabel("")
        bar.addWidget(self._status)
        bar.addWidget(self.btn_download)
        layout.addLayout(bar)

        if self._combo.count():
            self._load(self._combo.currentText())

    # --- model access ---
    def _names(self) -> list[str]:
        return sorted(set(list_datasets(self._root)) | set(preset_datasets()))

    def _dataset(self, name: str) -> DataSet:
        return load_dataset(name, self._root) or preset_datasets().get(name) or DataSet(name)

    def _load(self, name: str) -> None:
        d = self._dataset(name)
        self._symbols.setPlainText("\n".join(d.symbols))
        self._provider.setCurrentText(d.provider or "Auto")
        self._interval.setCurrentText(d.interval)

    # --- public (dialog-free) operations ---
    def current_dataset(self) -> DataSet:
        choice = self._provider.currentText()
        return DataSet(
            name=self._combo.currentText().strip(),
            symbols=parse_symbols(self._symbols.toPlainText()),
            provider=None if choice == "Auto" else choice,
            interval=self._interval.currentText(),
        )

    def save(self) -> DataSet:
        d = self.current_dataset()
        save_dataset(d, self._root)
        return d

    def new_dataset(self, name: str) -> DataSet:
        d = DataSet(name=name)
        save_dataset(d, self._root)
        if self._combo.findText(name) < 0:
            self._combo.addItem(name)
        self._combo.setCurrentText(name)
        return d

    # --- dialog handlers ---
    def _on_new(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New DataSet", "Name:")
        if ok and name.strip():
            self.new_dataset(name.strip())

    def _on_save(self) -> None:
        d = self.save()
        self._status.setText(f"saved {d.name} ({len(d.symbols)})")

    def _on_delete(self) -> None:
        name = self._combo.currentText()
        delete_dataset(name, self._root)
        idx = self._combo.currentIndex()
        if idx >= 0:
            self._combo.removeItem(idx)

    def _on_download_all(self) -> None:
        if self._on_download is None:
            return
        self._on_download(self.save(), self._days.value())
