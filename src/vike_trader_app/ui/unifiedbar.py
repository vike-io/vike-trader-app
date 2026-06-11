"""Shared title-bar chrome (unified title bar, stage 1).

ONE glyph-button factory + ONE bar widget, used by every title-bar surface so they render
identical MultiCharts-style chrome:
  * the chart-space header (dockshell.VikeDockTitleBar, central area)
  * the floating chart windows (chartwin.ChartWindowFrame)
Side panels keep the real ADS title bar (restyled to the same MC button set) — they don't
use UnifiedTitleBar, but they share bar_button's look via QSS.

The MC title is a single clean line ([icon] NAME … ⧉ ─ □ ✕), NOT a tab strip. We OVERCOME
MC by making that line LIVE (e.g. "CHART · BTCUSDT · 1m · 62,403 ▲0.18%") — set_title() is
called on space / symbol / price change.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

BAR_H = 30   # one height for every title-bar surface (matches ads--CDockAreaTitleBar QSS)


def bar_button(glyph: str, tip: str, slot=None, danger: bool = False,
               width: int = 36, parent=None) -> QtWidgets.QToolButton:
    """The one glyph-button factory for title bars (was duplicated in chartwin._btn and the
    main caption's window buttons). danger=True gives the Windows-red close hover."""
    b = QtWidgets.QToolButton(parent)
    b.setText(glyph)
    b.setToolTip(tip)
    b.setFixedSize(width, BAR_H)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    hover = "#c42b1c" if danger else theme.PANEL
    b.setStyleSheet(
        f"QToolButton{{border:none;background:transparent;color:{theme.TEXT2};"
        f"font-size:12px;}}"
        f"QToolButton:hover{{background:{hover};color:{theme.TEXT};}}")
    if slot is not None:
        b.clicked.connect(slot)
    return b


class UnifiedTitleBar(QtWidgets.QWidget):
    """The shared 30px bar: [icon][title][...adopted widgets...][buttons].

    Labels are mouse-transparent so a host's drag eventFilter on the bar keeps working;
    buttons are registered by key for later glyph/tooltip swaps (max<->restore, detach)."""

    def __init__(self, title: str = "", icon: "QtGui.QPixmap | None" = None, parent=None):
        super().__init__(parent)
        self.setObjectName("unifiedBar")
        self.setFixedHeight(BAR_H)
        self._buttons: dict[str, QtWidgets.QToolButton] = {}
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(6)
        self._icon = QtWidgets.QLabel()
        self._icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        if icon is not None:
            self._icon.setPixmap(icon)
        else:
            self._icon.hide()
        lay.addWidget(self._icon)
        self._title = QtWidgets.QLabel(title)
        self._title.setObjectName("unifiedBarTitle")
        self._title.setStyleSheet(
            f"#unifiedBarTitle{{color:{theme.TEXT};font-size:12px;font-weight:600;"
            f"background:transparent;}}")
        self._title.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._title)
        lay.addStretch(1)
        self._lay = lay
        self.set_active(False)

    def set_icon(self, pixmap: "QtGui.QPixmap | None") -> None:
        if pixmap is None:
            self._icon.hide()
        else:
            self._icon.setPixmap(pixmap)
            self._icon.show()

    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_title_rich(self, html: str) -> None:
        """Live ticker title: lets the price carry its own green/red colour via rich text."""
        self._title.setTextFormat(QtCore.Qt.RichText)
        self._title.setText(html)

    def add_widget(self, w: QtWidgets.QWidget) -> None:
        """Adopt an external widget (e.g. the doc's keep-on-top pin) just before the buttons."""
        # insert before the trailing stretch is unnecessary — buttons are added after; keep it
        # simple: widgets/buttons append in call order, all sit right of the stretch.
        self._lay.addWidget(w)

    def add_button(self, key: str, glyph: str, tip: str, slot,
                   danger: bool = False) -> QtWidgets.QToolButton:
        b = bar_button(glyph, tip, slot, danger, parent=self)
        self._buttons[key] = b
        self._lay.addWidget(b)
        return b

    def button(self, key: str) -> "QtWidgets.QToolButton | None":
        return self._buttons.get(key)

    def set_active(self, on: bool) -> None:
        # scoped: a bare "background:" would cascade into popup QMenus (known bug class)
        self.setStyleSheet(
            f"#unifiedBar{{background:{theme.RAISE if on else theme.SURFACE};"
            f"border-bottom:1px solid {theme.BORDER};}}")
