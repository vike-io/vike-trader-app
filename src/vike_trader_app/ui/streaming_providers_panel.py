"""Streaming Providers panel: informational push/poll classification + persisted enable toggle.

Each row shows "<name>  ·  <push|poll>" — the kind comes from whether the source's
Source.supports_live_ws is True. The enable toggle persists to storage/streaming_providers.json.

Live routing is still owned by select_source; this panel is informational + a future hook.
"""

from PySide6 import QtCore, QtWidgets

from ..data.streaming_providers_config import (
    StreamingProviderEntry,
    StreamingProvidersConfig,
    load_streaming_providers_config,
    save_streaming_providers_config,
    streaming_kind,
)


def _row_label(name: str) -> str:
    """Format a list-row label: 'binance  ·  push'."""
    return f"{name}  ·  {streaming_kind(name)}"


class StreamingProvidersPanel(QtWidgets.QWidget):
    """Checkable streaming-provider list.

    Each row is labelled '<name>  ·  <push|poll>'. Checkbox = enabled. Persists to
    ``<root>/streaming_providers.json`` on every check-change.

    Public API (dialog-free, for tests):
        current_config() -> StreamingProvidersConfig
        enabled_names()  -> list[str]
        set_enabled(name, bool)
    """

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Live routing is chosen automatically by symbol; "
            "push sources use a WebSocket, poll sources fetch latest bars."
        ))

        self._list = QtWidgets.QListWidget()
        self._list.itemChanged.connect(lambda *_: self._persist())
        layout.addWidget(self._list, 1)

        cfg = load_streaming_providers_config(root)
        self._populate(cfg)

    def _populate(self, cfg: StreamingProvidersConfig) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for p in cfg.providers:
            item = QtWidgets.QListWidgetItem(_row_label(p.name))
            item.setData(QtCore.Qt.UserRole, p.name)  # store the bare name for lookup
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if p.enabled else QtCore.Qt.Unchecked)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def current_config(self) -> StreamingProvidersConfig:
        entries = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            name = it.data(QtCore.Qt.UserRole)
            entries.append(StreamingProviderEntry(name, it.checkState() == QtCore.Qt.Checked))
        return StreamingProvidersConfig(entries)

    def enabled_names(self) -> list[str]:
        return self.current_config().enabled_in_order()

    def set_enabled(self, name: str, enabled: bool) -> None:
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.data(QtCore.Qt.UserRole) == name:
                it.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
                return

    def _persist(self) -> None:
        save_streaming_providers_config(self.current_config(), self._root)
