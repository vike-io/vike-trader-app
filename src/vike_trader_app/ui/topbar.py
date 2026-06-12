"""Top command/launcher bar (S2 of the shell-UX plan — the MultiCharts-16 row).

A slim strip above the dock area: a VS-Code-style **menu bar** (File/View/Go/… — installed via
``set_menu_bar()``), a **symbol-or-command box** (MC's Command Line / TradingView's search —
type ``ETHUSDT`` to load a symbol, ``5m`` to switch interval, anything else fuzzy-runs a palette
command), and a right cluster of **window-type icon launchers** hosted in a QToolBar (whose
built-in ``»`` extension gives overflow for free).

``classify()`` is the pure, Qt-free resolver (unit-tested without a QApplication).
"""

from __future__ import annotations

import re

from PySide6 import QtCore, QtGui, QtWidgets

from . import icons, theme
from .fuzzy import filter_items

INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"}
_SYMBOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9:._-]{1,14}$")


def classify(text: str, command_labels=()) -> tuple[str, str]:
    """Resolve box input -> ("interval", iv) | ("command", label) | ("symbol", SYM) | ("none", "").

    Order matters: exact interval tokens first ("1d" is an interval, not a symbol); then a
    fuzzy command match ONLY when the text doesn't look like a plain ticker (so "ETHUSDT" never
    runs a command); ticker-shaped text becomes an uppercased symbol; everything else falls back
    to the best fuzzy command, else none."""
    t = text.strip()
    if not t:
        return ("none", "")
    if t.lower() in INTERVALS:
        return ("interval", t.lower())
    looks_symbol = _SYMBOL_RE.match(t) and " " not in t
    matches = filter_items(t, [(c, c) for c in command_labels])
    if matches and not looks_symbol:
        return ("command", matches[0][0])
    if looks_symbol:
        return ("symbol", t.upper())
    if matches:
        return ("command", matches[0][0])
    return ("none", "")


class CommandBar(QtWidgets.QWidget):
    """The strip: menu bar | symbol-or-command box | window-type launchers (with » overflow)."""

    symbolSubmitted = QtCore.Signal(str)
    intervalSubmitted = QtCore.Signal(str)
    commandSubmitted = QtCore.Signal(str)     # a palette command label to execute

    def __init__(self, commands_provider, parent=None):
        super().__init__(parent)
        self._commands_provider = commands_provider   # () -> [(label, callback)] (the palette's)
        self.setFixedHeight(30)
        # scoped: a bare "background:…" would cascade into child popups (menus etc.)
        self.setStyleSheet(f"CommandBar{{background:{theme.BG};}}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 8, 0)
        lay.setSpacing(8)
        self._lay = lay

        # the menu bar (File/View/Go/…) docks at index 0 via set_menu_bar() — built in menus.py
        self.menubar: QtWidgets.QMenuBar | None = None

        # the symbol-or-command box (centered, capped width — the MC16/Tradovate look)
        self.box = QtWidgets.QLineEdit()
        self.box.setPlaceholderText("Type symbol or command…   ( / )")
        self.box.setClearButtonEnabled(True)
        self.box.setFixedHeight(24)
        self.box.setMaximumWidth(520)
        self.box.setStyleSheet(
            f"QLineEdit{{background:{theme.RAISE};border:1px solid {theme.BORDER};"
            f"border-radius:6px;padding:0 10px;color:{theme.TEXT};font-size:12px;}}"
            f"QLineEdit:focus{{border-color:{theme.ACCENT};}}"
        )
        self.box.returnPressed.connect(self._submit)
        self._history: list[str] = []
        comp = QtWidgets.QCompleter([], self.box)
        comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.box.setCompleter(comp)
        lay.addStretch(1)
        lay.addWidget(self.box, 2)
        lay.addStretch(1)

        # window-type launchers — QToolBar gives the » extension (overflow) for free
        self.launchers = QtWidgets.QToolBar()
        self.launchers.setIconSize(QtCore.QSize(18, 18))
        self.launchers.setMovable(False)
        self.launchers.setStyleSheet(
            f"QToolBar{{border:none;background:transparent;spacing:2px;}}"
            f"QToolButton{{border:none;border-radius:6px;padding:3px;}}"
            f"QToolButton:hover{{background:{theme.PANEL};}}"
        )
        lay.addWidget(self.launchers)

        QtGui.QShortcut(QtGui.QKeySequence("/"), self, activated=self._focus_box,
                        context=QtCore.Qt.ApplicationShortcut)

    # --- behaviour ----------------------------------------------------------------------

    def set_menu_bar(self, bar: QtWidgets.QMenuBar) -> None:
        """Dock the File/View/Go/… menu bar at the left end of the strip (VS Code layout)."""
        self.menubar = bar
        bar.setParent(self)
        self._lay.insertWidget(0, bar, 0, QtCore.Qt.AlignVCenter)

    def add_launcher(self, icon_name: str, tooltip: str, callback) -> QtGui.QAction:
        # Each launcher carries its tool's DISTINCT colour (toolreg.TOOL_COLORS) so the top-right
        # cluster reads like a colourful toolbar. Launchers aren't checkable, so the RESTING
        # (Normal/Off) colour is what shows: pass the tool colour there too (not the dim TEXT3),
        # with a lightened hover variant. Falls back to the accent for any unmapped icon.
        from .toolreg import tool_color, tool_hover_color
        c = tool_color(icon_name, theme.ACCENT)
        act = QtGui.QAction(
            icons.rail_icon(icon_name, c, c, tool_hover_color(icon_name, theme.TEXT2)),
            tooltip, self)
        act.triggered.connect(callback)
        self.launchers.addAction(act)
        return act

    def _focus_box(self) -> None:
        # "/" anywhere focuses the box — unless the user is typing in another editor
        fw = QtWidgets.QApplication.focusWidget()
        if isinstance(fw, (QtWidgets.QLineEdit, QtWidgets.QTextEdit, QtWidgets.QPlainTextEdit)) \
                and fw is not self.box:
            return
        self.box.setFocus()
        self.box.selectAll()

    def _submit(self) -> None:
        text = self.box.text()
        labels = [label for label, _cb in self._commands_provider()]
        kind, value = classify(text, labels)
        if kind == "none":
            return
        if text not in self._history:
            self._history.insert(0, text)
            del self._history[12:]
            self.box.completer().setModel(QtCore.QStringListModel(self._history))
        self.box.clear()
        if kind == "symbol":
            self.symbolSubmitted.emit(value)
        elif kind == "interval":
            self.intervalSubmitted.emit(value)
        else:
            self.commandSubmitted.emit(value)
