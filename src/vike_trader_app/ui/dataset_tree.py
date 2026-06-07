"""DataSets tree (left pane): provider group nodes + a 'My DataSets' folder.

Grouping is derived from each DataSet's linked provider / symbols (see datasets.provider_group),
not stored. Mutating ops are dialog-free for testability; the host wires the ＋ button / context menu.
"""

from PySide6 import QtCore, QtWidgets

from ..data.datasets import (
    DataSet,
    delete_dataset,
    list_datasets,
    load_dataset,
    preset_datasets,
    provider_group,
    save_dataset,
)

_GROUPS = ["All", "Binance", "Dukascopy", "My DataSets"]


class DataSetTree(QtWidgets.QWidget):
    """Tree of DataSets grouped by provider. Emits names on selection."""

    dataset_selected = QtCore.Signal(str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = root
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.btn_new = QtWidgets.QPushButton("＋ New DataSet")
        self.btn_new.clicked.connect(self._on_new)
        layout.addWidget(self.btn_new)
        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(180)  # else nested DataSet names elide to an ambiguous "Crypto …"
        self._tree.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self._tree, 1)

    def _all_datasets(self) -> list[DataSet]:
        names = sorted(set(list_datasets(self._root)) | set(preset_datasets()))
        return [load_dataset(n, self._root) or preset_datasets().get(n) or DataSet(n) for n in names]

    def reload(self) -> None:
        self._tree.clear()
        buckets: dict[str, list[str]] = {g: [] for g in _GROUPS}
        for d in self._all_datasets():
            buckets["All"].append(d.name)
            g = provider_group(d)
            if g in buckets:
                buckets[g].append(d.name)
            if d.provider is None:
                buckets["My DataSets"].append(d.name)
        for g in _GROUPS:
            node = QtWidgets.QTreeWidgetItem([g])
            for name in buckets[g]:
                node.addChild(QtWidgets.QTreeWidgetItem([name]))
            self._tree.addTopLevelItem(node)
            node.setExpanded(True)

    def node_names(self, group: str) -> list[str]:
        """Child dataset names under ``group`` (test/host helper)."""
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            if top.text(0) == group:
                return [top.child(j).text(0) for j in range(top.childCount())]
        return []

    def create_dataset(self, name: str) -> DataSet:
        """Persist an empty DataSet, reload, and emit selection (dialog-free)."""
        d = DataSet(name=name)
        save_dataset(d, self._root)
        self.reload()
        self.dataset_selected.emit(name)
        return d

    def remove_dataset(self, name: str) -> None:
        delete_dataset(name, self._root)
        self.reload()

    def _on_new(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "New DataSet", "Name:")
        if ok and name.strip():
            self.create_dataset(name.strip())

    def _on_current_changed(self, cur, _prev) -> None:
        if cur is not None and cur.parent() is not None:  # a dataset leaf, not a group node
            self.dataset_selected.emit(cur.text(0))
