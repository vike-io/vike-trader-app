"""Indicator catalogue dialog — browse indicators by category, preview the snippet, insert it.

Opened from the Studio toolbar; emits ``insertRequested(snippet)`` which the Studio appends to
the code editor. Snippets are the vetted, preflight-clean helpers in ``analysis.indicator_catalog``.
"""

from PySide6 import QtCore, QtWidgets

from ..analysis.indicator_catalog import CATALOG
from . import theme


class IndicatorCatalogDialog(QtWidgets.QDialog):
    """Left: a category tree of indicators. Right: description + snippet + 'Insert into editor'."""

    insertRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Indicator catalogue")
        self.resize(760, 500)
        root = QtWidgets.QHBoxLayout(self)

        self._tree = QtWidgets.QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(220)
        cats: dict[str, QtWidgets.QTreeWidgetItem] = {}
        for ind in CATALOG:
            cat = cats.get(ind.category)
            if cat is None:
                cat = QtWidgets.QTreeWidgetItem([ind.category])
                self._tree.addTopLevelItem(cat)
                cats[ind.category] = cat
            child = QtWidgets.QTreeWidgetItem([ind.name])
            child.setData(0, QtCore.Qt.UserRole, ind)
            cat.addChild(child)
        self._tree.expandAll()
        self._tree.currentItemChanged.connect(self._on_select)
        root.addWidget(self._tree)

        right = QtWidgets.QVBoxLayout()
        self._title = QtWidgets.QLabel("Select an indicator")
        self._title.setStyleSheet(f"color:{theme.TEXT};font-weight:700;font-size:14px;")
        self._desc = QtWidgets.QLabel()
        self._desc.setWordWrap(True)
        self._desc.setStyleSheet(f"color:{theme.TEXT2};font-size:11px;")
        self._code = QtWidgets.QPlainTextEdit()
        self._code.setReadOnly(True)
        self._code.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.PANEL};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:6px;font-family:monospace;}}"
        )
        right.addWidget(self._title)
        right.addWidget(self._desc)
        right.addWidget(self._code, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_insert = QtWidgets.QPushButton("Insert into editor")
        self._btn_insert.setObjectName("play")
        self._btn_insert.setEnabled(False)
        self._btn_insert.clicked.connect(self._insert)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_insert)
        btn_row.addWidget(close)
        right.addLayout(btn_row)
        root.addLayout(right, 1)

        self._current = None
        first_cat = self._tree.topLevelItem(0)
        if first_cat is not None and first_cat.childCount():
            self._tree.setCurrentItem(first_cat.child(0))

    def _on_select(self, item, _prev) -> None:
        ind = item.data(0, QtCore.Qt.UserRole) if item is not None else None
        self._current = ind
        if ind is None:
            self._btn_insert.setEnabled(False)
            return
        self._title.setText(f"{ind.name}   ·   {ind.category}")
        self._desc.setText(f"{ind.description}   (params: {ind.params})")
        self._code.setPlainText(ind.snippet)
        self._btn_insert.setEnabled(True)

    def _insert(self) -> None:
        if self._current is not None:
            self.insertRequested.emit(self._current.snippet)
            self.accept()
