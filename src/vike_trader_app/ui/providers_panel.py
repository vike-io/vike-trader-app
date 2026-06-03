"""Historical Providers panel: enable (checkbox) + priority (drag order) + a Load-Data testbed.

Mirrors Wealth-Lab's Historical Providers tab. The order/flags persist to storage/providers.json
and drive the provider fallback chain used by Auto routing. Public ops are dialog-free for tests.
"""

import time

from PySide6 import QtCore, QtWidgets

from ..data.providers_config import (
    ProviderEntry,
    ProvidersConfig,
    load_providers_config,
    save_providers_config,
)

_INTERVALS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"]


class ProvidersPanel(QtWidgets.QWidget):
    """Reorderable, checkable provider list + testbed. Emits a one-line testbed report string."""

    testbed_result = QtCore.Signal(str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Historical Providers — check to enable, drag to prioritize"))

        self._list = QtWidgets.QListWidget()
        self._list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self._list.model().rowsMoved.connect(lambda *_: self._persist())
        self._list.itemChanged.connect(lambda *_: self._persist())
        layout.addWidget(self._list, 1)
        self._populate(load_providers_config(root))

        # --- Load-Data testbed ---
        tb = QtWidgets.QHBoxLayout()
        self._tb_symbol = QtWidgets.QLineEdit()
        self._tb_symbol.setPlaceholderText("symbol e.g. BTCUSDT")
        self._tb_interval = QtWidgets.QComboBox()
        self._tb_interval.addItems(_INTERVALS)
        self.btn_load = QtWidgets.QPushButton("Load Data")
        self.btn_load.clicked.connect(self._on_load)
        tb.addWidget(self._tb_symbol, 1)
        tb.addWidget(self._tb_interval)
        tb.addWidget(self.btn_load)
        layout.addLayout(tb)

    def _populate(self, cfg: ProvidersConfig) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for p in cfg.providers:
            item = QtWidgets.QListWidgetItem(p.name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if p.enabled else QtCore.Qt.Unchecked)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def current_config(self) -> ProvidersConfig:
        entries = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            entries.append(ProviderEntry(it.text(), it.checkState() == QtCore.Qt.Checked))
        return ProvidersConfig(entries)

    def current_order(self) -> list[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def enabled_names(self) -> list[str]:
        return self.current_config().enabled_in_order()

    def set_enabled(self, name: str, enabled: bool) -> None:
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.text() == name:
                it.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
                return

    def _persist(self) -> None:
        save_providers_config(self.current_config(), self._root)

    def run_testbed(self, symbol: str, interval: str, fetch=None) -> None:
        """Try to load ``symbol`` via the chain; emit a one-line report. ``fetch`` injectable for tests."""
        if fetch is None:
            from ..data.provider_chain import fetch_for

            def fetch(sym, iv):
                now = int(time.time() * 1000)
                return fetch_for(sym, iv, now - 7 * 86_400_000, now, root=self._root)

        try:
            bars, used = fetch(symbol, interval)
        except Exception as exc:  # noqa: BLE001 - report failures, don't crash
            self.testbed_result.emit(f"{symbol} {interval}: error — {exc}")
            return
        if bars:
            self.testbed_result.emit(f"{symbol} {interval}: {len(bars)} bars via {used}")
        else:
            self.testbed_result.emit(f"{symbol} {interval}: no data from any enabled provider")

    def _on_load(self) -> None:
        sym = self._tb_symbol.text().strip()
        if sym:
            self.run_testbed(sym, self._tb_interval.currentText())
