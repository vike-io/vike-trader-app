"""Ctrl+K command palette (Phase 5).

A frameless quick-open over the window: type to fuzzy-filter a flat list of commands, ↑/↓ to
move, Enter to run, Esc to close. Commands are ``(label, callback)`` pairs supplied by the
shell (switch space, open workspace, new chart, AI layout, …). The ranking lives in the
Qt-free ``fuzzy`` module; this is just the surface.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from . import theme
from .fuzzy import filter_items


class CommandPalette(QtWidgets.QDialog):
    def __init__(self, commands, parent=None):
        super().__init__(parent)
        self._commands = list(commands)          # [(label, callback)]
        self._filtered: list = []
        self.setWindowFlags(QtCore.Qt.Popup | QtCore.Qt.FramelessWindowHint)
        self.setModal(True)
        self.setFixedWidth(560)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        card = QtWidgets.QWidget()
        card.setObjectName("palette")
        card.setStyleSheet(
            f"#palette{{background:{theme.CHART_BG};border:1px solid {theme.BORDER};"
            f"border-radius:12px;}}"
        )
        inner = QtWidgets.QVBoxLayout(card)
        inner.setContentsMargins(10, 10, 10, 10)
        inner.setSpacing(8)

        self._edit = QtWidgets.QLineEdit()
        self._edit.setPlaceholderText("Type a command…   (↑↓ to move, ↵ to run, Esc to close)")
        self._edit.setStyleSheet(
            f"QLineEdit{{background:{theme.RAISE};border:1px solid {theme.BORDER};"
            f"border-radius:8px;padding:8px 10px;color:{theme.TEXT};font-size:14px;}}"
        )
        self._list = QtWidgets.QListWidget()
        self._list.setStyleSheet(
            f"QListWidget{{background:transparent;border:none;color:{theme.TEXT2};font-size:13px;}}"
            f"QListWidget::item{{padding:6px 8px;border-radius:6px;}}"
            f"QListWidget::item:selected{{background:{theme.RAISE};color:{theme.TEXT};}}"
        )
        self._list.setUniformItemSizes(True)
        self._list.itemActivated.connect(lambda _i: self._run_current())
        inner.addWidget(self._edit)
        inner.addWidget(self._list)
        lay.addWidget(card)

        self._edit.textChanged.connect(self._refilter)
        self._edit.installEventFilter(self)      # ↑/↓/↵/Esc while typing
        self._refilter("")

    # --- behaviour --------------------------------------------------------------------------

    def _refilter(self, text: str) -> None:
        self._filtered = filter_items(text, self._commands)
        self._list.clear()
        self._list.addItems([label for label, _cb in self._filtered])
        if self._list.count():
            self._list.setCurrentRow(0)

    def _run_current(self) -> None:
        row = self._list.currentRow()
        if 0 <= row < len(self._filtered):
            callback = self._filtered[row][1]
            self.accept()
            callback()                            # run AFTER closing so it can open dialogs

    def _move(self, delta: int) -> None:
        n = self._list.count()
        if n:
            self._list.setCurrentRow((self._list.currentRow() + delta) % n)

    def eventFilter(self, obj, event):
        if obj is self._edit and event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key_Down,):
                self._move(1)
                return True
            if key in (QtCore.Qt.Key_Up,):
                self._move(-1)
                return True
            if key in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
                self._run_current()
                return True
            if key == QtCore.Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)

    # --- test/automation hooks --------------------------------------------------------------

    def set_query(self, text: str) -> None:
        self._edit.setText(text)

    def current_labels(self) -> list[str]:
        return [label for label, _cb in self._filtered]

    def activate(self, row: int = 0) -> None:
        self._list.setCurrentRow(row)
        self._run_current()
