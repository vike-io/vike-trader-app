"""Shared title-bar chrome (unified title bar).

ONE glyph-button factory + ONE bar widget, used by every title-bar surface so they render
identical MultiCharts-style chrome:
  * the chart-space header (dockshell.VikeDockTitleBar, central area)
  * the side panels (Market watch / Trades / …)
  * the floating chart windows (chartwin.ChartWindowFrame)

Layout: ``[icon] [title] [status box] [stretch] [button box]``. The status box (link dots,
feed badge) keeps small gaps; the button box is CONTIGUOUS (0 gap, Windows-style) so the
window controls read as one cluster, not scattered. The MC title is a single clean line — we
OVERCOME MC by making it LIVE (e.g. "CHART · BTCUSDT · 1m · 62,403 ▲0.18%") via set_title*.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from . import theme

BAR_H = 30          # one height for every title-bar surface (matches ads--CDockAreaTitleBar QSS)
_BTN_W = 40         # window-button width (contiguous, no gaps) — design "B"
_BTN_PX = 15        # window-button glyph size


def bar_button(glyph: str, tip: str, slot=None, danger: bool = False,
               width: int = _BTN_W, parent=None) -> QtWidgets.QToolButton:
    """The one glyph-button factory for title bars. danger=True gives the Windows-red close
    hover. Default 40×30 with a 15px glyph; buttons are placed contiguously (0 gap)."""
    b = QtWidgets.QToolButton(parent)
    b.setText(glyph)
    b.setToolTip(tip)
    b.setFixedSize(width, BAR_H)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    hover = "#c42b1c" if danger else theme.PANEL
    b.setStyleSheet(
        f"QToolButton{{border:none;background:transparent;color:{theme.TEXT2};"
        f"font-size:{_BTN_PX}px;}}"
        f"QToolButton:hover{{background:{hover};color:{theme.TEXT};}}")
    if slot is not None:
        b.clicked.connect(slot)
    return b


def update_max_button_state(button, maxed: bool) -> None:
    """Flip a title-bar maximize button between maximized (❐ + 'Restore') and normal
    (□ + 'Maximize / restore'). THE single place every title bar / frame flips this glyph + tooltip,
    so they can't drift apart (the tooltip text was inconsistent across sites before). No-op if the
    button is None."""
    if button is None:
        return
    button.setText("❐" if maxed else "□")
    button.setToolTip("Restore" if maxed else "Maximize / restore")


class FeedBadge(QtWidgets.QWidget):
    """Compact data-feed badge (● LIVE / ● CACHED / …) for a title bar. The dot is a DRAWN circle
    (not an inline '●' glyph, which renders high and rides above the text) vertically centred with
    a 14px label, so the whole badge sits on the same line as the title + chips. The host maps a
    feed state to (colour, text) and calls set_state — keeps this widget app-agnostic."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("feedBadge")
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)
        self._dot = QtWidgets.QLabel()
        self._dot.setFixedSize(8, 8)
        self._label = QtWidgets.QLabel()
        lay.addWidget(self._dot, 0, QtCore.Qt.AlignVCenter)
        lay.addWidget(self._label, 0, QtCore.Qt.AlignVCenter)
        self.set_state(theme.TEXT3, "")

    def set_state(self, color: str, text: str) -> None:
        label = text.lstrip("●").strip()   # tolerate a legacy leading "●" in the supplied text
        self._dot.setStyleSheet(f"background:{color};border-radius:4px;")
        self._label.setText(label)
        self._label.setStyleSheet(
            f"color:{color};font-size:12px;font-weight:200;background:transparent;")

    def text(self) -> str:
        """The label text (without the dot) — back-compat for callers/tests that read it."""
        return self._label.text()


class UnifiedTitleBar(QtWidgets.QWidget):
    """The shared 30px bar. Labels are mouse-transparent so a host's drag eventFilter on the
    bar keeps working; status widgets and buttons are interactive."""

    def __init__(self, title: str = "", icon: "QtGui.QPixmap | None" = None, parent=None):
        super().__init__(parent)
        self.setObjectName("unifiedBar")
        self.setFixedHeight(BAR_H)
        self._buttons: dict[str, QtWidgets.QToolButton] = {}
        self._menu_cb = None
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 0, 0)   # right margin 0 → close button touches the edge
        lay.setSpacing(6)
        self._icon = QtWidgets.QLabel()
        self._icon.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        if icon is not None:
            self._icon.setPixmap(icon)
        else:
            self._icon.hide()
        lay.addWidget(self._icon, 0, QtCore.Qt.AlignVCenter)
        self._title = QtWidgets.QLabel(title)
        self._title.setObjectName("unifiedBarTitle")
        self._title.setStyleSheet(
            f"#unifiedBarTitle{{color:{theme.TEXT};font-size:12px;font-weight:600;"
            f"background:transparent;}}")
        self._title.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self._title.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(self._title, 0, QtCore.Qt.AlignVCenter)
        # status box: link dots + feed badge, just right of the title (small gaps)
        self._statusbox = QtWidgets.QWidget()
        self._statusbox.setObjectName("unifiedStatus")
        self._statuslay = QtWidgets.QHBoxLayout(self._statusbox)
        self._statuslay.setContentsMargins(2, 0, 2, 0)
        self._statuslay.setSpacing(8)
        lay.addWidget(self._statusbox, 0, QtCore.Qt.AlignVCenter)
        lay.addStretch(1)
        # button box: the window controls, CONTIGUOUS (0 gap), hard against the right edge
        self._btnbox = QtWidgets.QWidget()
        self._btnbox.setObjectName("unifiedBtns")
        self._btnlay = QtWidgets.QHBoxLayout(self._btnbox)
        self._btnlay.setContentsMargins(0, 0, 0, 0)
        self._btnlay.setSpacing(0)
        lay.addWidget(self._btnbox)
        self._lay = lay
        self.set_active(False)

    def set_icon(self, pixmap: "QtGui.QPixmap | None") -> None:
        if pixmap is None:
            self._icon.hide()
        else:
            self._icon.setPixmap(pixmap)
            self._icon.show()

    def set_brand_button(self, btn: QtWidgets.QWidget) -> None:
        """Use an interactive button as the far-left brand mark (hides the static icon). Chart
        windows pass their chart-type selector here so the brand icon IS the style dropdown."""
        self._icon.hide()
        self._lay.insertWidget(0, btn, 0, QtCore.Qt.AlignVCenter)

    def tune_spacing(self, *, main: int, status: int, status_margin: int = 0) -> None:
        """Per-instance spacing override (chart windows space their title-bar chips ~20px apart)."""
        self._lay.setSpacing(main)
        self._statuslay.setSpacing(status)
        self._statuslay.setContentsMargins(status_margin, 0, status_margin, 0)

    def set_title(self, text: str) -> None:
        self._title.setTextFormat(QtCore.Qt.PlainText)
        self._title.setText(text)

    def set_title_rich(self, html: str) -> None:
        """Live ticker title: lets the price carry its own green/red colour via rich text."""
        self._title.setTextFormat(QtCore.Qt.RichText)
        self._title.setText(html)

    def style_title(self, *, px: int = 12, weight: int = 600, mono: bool = False) -> None:
        """Per-instance restyle of the title label (shared default is 12px / weight 600 /
        proportional). Chart windows use 14px / weight 300 so the symbol matches the title-bar
        interval picker beside it; this never touches tool/panel bars (each has its own instance)."""
        fam = f"font-family:{theme.FONT_MONO};" if mono else ""
        self._title.setStyleSheet(
            f"#unifiedBarTitle{{color:{theme.TEXT};font-size:{px}px;font-weight:{weight};"
            f"{fam}background:transparent;}}")

    def add_status(self, w: QtWidgets.QWidget) -> None:
        """Add a status widget (chip / feed badge) to the left cluster, after the title — vertically
        centred so mixed-size chips share one centre line."""
        self._statuslay.addWidget(w, 0, QtCore.Qt.AlignVCenter)

    def add_widget(self, w: QtWidgets.QWidget) -> None:
        """Adopt an external widget (e.g. the doc's keep-on-top pin) into the button cluster,
        just left of the window controls."""
        self._btnlay.addWidget(w)

    def add_button(self, key: str, glyph: str, tip: str, slot,
                   danger: bool = False) -> QtWidgets.QToolButton:
        b = bar_button(glyph, tip, slot, danger, parent=self)
        self._buttons[key] = b
        self._btnlay.addWidget(b)
        return b

    def button(self, key: str) -> "QtWidgets.QToolButton | None":
        return self._buttons.get(key)

    def set_active(self, on: bool) -> None:
        # scoped: a bare "background:" would cascade into popup QMenus (known bug class)
        self.setStyleSheet(
            f"#unifiedBar{{background:{theme.RAISE if on else theme.SURFACE};"
            f"border-bottom:1px solid {theme.BORDER};}}")

    def set_menu(self, cb) -> None:
        """cb(global_pos) builds + shows the title-bar right-click menu. Host-supplied so this
        widget stays app-agnostic (the menu's actions live with the host)."""
        self._menu_cb = cb

    def contextMenuEvent(self, ev):  # noqa: N802 - Qt override
        if self._menu_cb is not None:
            self._menu_cb(ev.globalPos())
        else:
            super().contextMenuEvent(ev)
