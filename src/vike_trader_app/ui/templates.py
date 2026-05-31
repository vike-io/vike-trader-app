"""Strategy-template gallery — pick a prebuilt strategy, preview it, load it into the editor.

Opened from the Studio toolbar; emits ``loadRequested(code)`` which the Studio loads into the
code editor (ready to Run + validate). Templates live in ``analysis.strategy_templates``.
"""

from PySide6 import QtCore, QtWidgets

from ..analysis.strategy_templates import TEMPLATES
from . import theme


class StrategyTemplateDialog(QtWidgets.QDialog):
    """Left: a list of templates. Right: the full strategy code + 'Load into editor'."""

    loadRequested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Strategy templates")
        self.resize(840, 560)
        root = QtWidgets.QHBoxLayout(self)

        self._list = QtWidgets.QListWidget()
        self._list.setMinimumWidth(250)
        self._list.setStyleSheet(f"QListWidget::item{{padding:6px 4px;border-bottom:1px solid {theme.BORDER};}}")
        for t in TEMPLATES:
            item = QtWidgets.QListWidgetItem(f"{t.name}\n   {t.category} · {t.description}")
            item.setData(QtCore.Qt.UserRole, t)
            self._list.addItem(item)
        self._list.currentItemChanged.connect(self._on_select)
        root.addWidget(self._list)

        right = QtWidgets.QVBoxLayout()
        self._title = QtWidgets.QLabel("Select a template")
        self._title.setStyleSheet(f"color:{theme.TEXT};font-weight:700;font-size:14px;")
        self._code = QtWidgets.QPlainTextEdit()
        self._code.setReadOnly(True)
        self._code.setStyleSheet(
            f"QPlainTextEdit{{background:{theme.PANEL};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:6px;font-family:monospace;}}"
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
        if TEMPLATES:
            self._list.setCurrentRow(0)

    def _on_select(self, item, _prev) -> None:
        t = item.data(QtCore.Qt.UserRole) if item is not None else None
        self._current = t
        if t is None:
            self._btn_load.setEnabled(False)
            return
        self._title.setText(f"{t.name}   ·   {t.category}")
        self._code.setPlainText(t.code)
        self._btn_load.setEnabled(True)

    def _load(self) -> None:
        if self._current is not None:
            self.loadRequested.emit(self._current.code)
            self.accept()
