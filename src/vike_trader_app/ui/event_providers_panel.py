"""Event Providers panel: enable (checkbox) + priority (drag order) for event data sources.

Mirrors Wealth-Lab's Event Providers tab. The order/flags persist to storage/event_providers.json
and drive which news + calendar providers are active. Public ops are dialog-free for tests.
"""

from PySide6 import QtCore, QtWidgets

from ..data.event_providers_config import (
    EventProviderEntry,
    EventProvidersConfig,
    load_event_providers_config,
    save_event_providers_config,
)


class EventProvidersPanel(QtWidgets.QWidget):
    """Reorderable, checkable event-provider list.

    Persists to ``<root>/event_providers.json`` on every check-change or reorder.
    """

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(
            "Event Providers — check to enable, drag to prioritize.\n"
            "Controls which news feeds and economic-calendar actuals sources are active."
        ))

        self._list = QtWidgets.QListWidget()
        self._list.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self._list.model().rowsMoved.connect(lambda *_: self._persist())
        self._list.itemChanged.connect(lambda *_: self._persist())
        layout.addWidget(self._list, 1)

        cfg = load_event_providers_config(root)
        self._populate(cfg)

    def _populate(self, cfg: EventProvidersConfig) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for p in cfg.providers:
            item = QtWidgets.QListWidgetItem(p.name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if p.enabled else QtCore.Qt.Unchecked)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def current_config(self) -> EventProvidersConfig:
        entries = []
        for i in range(self._list.count()):
            it = self._list.item(i)
            entries.append(EventProviderEntry(it.text(), it.checkState() == QtCore.Qt.Checked))
        return EventProvidersConfig(entries)

    def enabled_names(self) -> list[str]:
        return self.current_config().enabled_in_order()

    def set_enabled(self, name: str, enabled: bool) -> None:
        for i in range(self._list.count()):
            it = self._list.item(i)
            if it.text() == name:
                it.setCheckState(QtCore.Qt.Checked if enabled else QtCore.Qt.Unchecked)
                return

    def _persist(self) -> None:
        save_event_providers_config(self.current_config(), self._root)
