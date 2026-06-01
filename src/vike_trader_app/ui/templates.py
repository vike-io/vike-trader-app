"""Strategy-template gallery — pick a prebuilt strategy, preview it, load it into the editor.

Opened from the Studio toolbar; emits ``loadRequested(code)`` which the Studio loads into the
code editor (ready to Run + validate). Built-in templates live in ``analysis.strategy_templates``;
the right-click menu (Duplicate / Rename / Delete) manages **session-local** user copies — the
built-ins themselves are protected and never mutated. (Mirrors TradeLocker's bot context menu;
persistence of user copies is a future enhancement.)
"""

from dataclasses import replace

from PySide6 import QtCore, QtWidgets

from ..analysis.strategy_templates import TEMPLATES
from . import theme

_BUILTIN_ROLE = QtCore.Qt.UserRole + 1


class StrategyTemplateDialog(QtWidgets.QDialog):
    """Left: a list of templates (with a context menu). Right: code + 'Load into editor'."""

    loadRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Strategy templates")
        self.resize(840, 560)
        root = QtWidgets.QHBoxLayout(self)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(6)
        hint = QtWidgets.QLabel("Right-click for Duplicate · Rename · Delete")
        hint.setStyleSheet(f"color:{theme.TEXT3};font-size:10px;")
        left.addWidget(hint)

        self._list = QtWidgets.QListWidget()
        self._list.setMinimumWidth(250)
        self._list.setStyleSheet(
            f"QListWidget::item{{padding:6px 4px;border-bottom:1px solid {theme.BORDER};}}"
        )
        self._list.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_menu)
        for t in TEMPLATES:
            self._add_item(t, builtin=True)
        self._list.currentItemChanged.connect(self._on_select)
        left.addWidget(self._list, 1)
        root.addLayout(left)

        right = QtWidgets.QVBoxLayout()
        self._title = QtWidgets.QLabel("Select a template")
        self._title.setStyleSheet(f"color:{theme.TEXT};font-weight:700;font-size:14px;")
        self._code = QtWidgets.QPlainTextEdit()
        self._code.setReadOnly(True)
        self._code.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.PANEL};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:6px;font-family:{theme.FONT_MONO};}}"
        )
        right.addWidget(self._title)
        right.addWidget(self._code, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_load = QtWidgets.QPushButton("Load into editor")
        self._btn_load.setObjectName("play")
        self._btn_load.setEnabled(False)
        self._btn_load.clicked.connect(self._load)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_load)
        btn_row.addWidget(close)
        right.addLayout(btn_row)
        root.addLayout(right, 1)

        self._current = None
        if self._list.count():
            self._list.setCurrentRow(0)

    # --- items ---

    def _add_item(self, tpl, *, builtin: bool, row: int | None = None) -> QtWidgets.QListWidgetItem:
        suffix = "" if builtin else "  ·  copy"
        item = QtWidgets.QListWidgetItem(f"{tpl.name}{suffix}\n   {tpl.category} · {tpl.description}")
        item.setData(QtCore.Qt.UserRole, tpl)
        item.setData(_BUILTIN_ROLE, builtin)
        if row is None:
            self._list.addItem(item)
        else:
            self._list.insertItem(row, item)
        return item

    def _on_select(self, item, _prev) -> None:
        t = item.data(QtCore.Qt.UserRole) if item is not None else None
        self._current = t
        if t is None:
            self._btn_load.setEnabled(False)
            return
        self._title.setText(f"{t.name}   ·   {t.category}")
        self._code.setPlainText(t.code)
        self._btn_load.setEnabled(True)

    # --- context menu (Duplicate / Rename / Delete) ---

    def _show_menu(self, pos: QtCore.QPoint) -> None:
        item = self._list.itemAt(pos)
        if item is None:
            return
        self._list.setCurrentItem(item)
        builtin = bool(item.data(_BUILTIN_ROLE))
        menu = QtWidgets.QMenu(self)
        act_dup = menu.addAction("Duplicate")
        act_rename = menu.addAction("Rename")
        act_delete = menu.addAction("Delete")
        act_rename.setEnabled(not builtin)   # built-ins are protected
        act_delete.setEnabled(not builtin)
        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == act_dup:
            self._duplicate(item)
        elif chosen == act_rename:
            self._rename(item)
        elif chosen == act_delete:
            self._delete(item)

    def _duplicate(self, item) -> None:
        tpl = item.data(QtCore.Qt.UserRole)
        copy = replace(tpl, name=f"{tpl.name}")
        row = self._list.row(item) + 1
        new_item = self._add_item(copy, builtin=False, row=row)
        self._list.setCurrentItem(new_item)

    def _rename(self, item) -> None:
        tpl = item.data(QtCore.Qt.UserRole)
        name, ok = QtWidgets.QInputDialog.getText(self, "Rename template", "New name:", text=tpl.name)
        name = name.strip()
        if not ok or not name:
            return
        updated = replace(tpl, name=name)
        item.setData(QtCore.Qt.UserRole, updated)
        item.setText(f"{name}  ·  copy\n   {updated.category} · {updated.description}")
        if item is self._list.currentItem():
            self._on_select(item, None)

    def _delete(self, item) -> None:
        row = self._list.row(item)
        self._list.takeItem(row)

    def _load(self) -> None:
        if self._current is not None:
            self.loadRequested.emit(self._current.code)
            self.accept()
