"""Custom window title bar (S6 of the shell-UX plan) — the MultiCharts-16 title row.

ONE bar IN the title area (not below it): [brand] [File/View/Go… menu bar] … [symbol-or-command box] …
[window-type launchers] [─][□][✕]. The window goes frameless on Windows and this bar becomes
the caption; a native event filter (``FramelessWindowFilter``) keeps EVERYTHING the OS gives a
normal window — drag-to-move, Aero Snap, edge resize, double-click maximize — by answering
``WM_NCHITTEST`` (caption/borders) and ``WM_NCCALCSIZE`` (client covers the frame).

Escape hatch: ``VIKE_NATIVE_TITLEBAR=1`` keeps the native OS caption and shows this bar below
it without the window buttons (the pre-S6 layout). Non-Windows platforms use that fallback
automatically for now.
"""

from __future__ import annotations

import ctypes
import os
import sys

if sys.platform == "win32":   # ctypes.wintypes raises on POSIX; only the filter needs it
    import ctypes.wintypes

from PySide6 import QtCore, QtWidgets

from . import icons, theme

TITLEBAR_H = 32   # VS-Code-slim caption row (was 40)
_RESIZE_BORDER = 8

# Win32 hit-test codes
_HTCLIENT, _HTCAPTION = 1, 2
_HTLEFT, _HTRIGHT, _HTTOP, _HTTOPLEFT, _HTTOPRIGHT = 10, 11, 12, 13, 14
_HTBOTTOM, _HTBOTTOMLEFT, _HTBOTTOMRIGHT = 15, 16, 17
_WM_NCCALCSIZE, _WM_NCHITTEST = 0x0083, 0x0084


def frameless_enabled() -> bool:
    """Frameless custom-caption mode: Windows-only for now, with an env escape hatch."""
    return sys.platform == "win32" and not os.environ.get("VIKE_NATIVE_TITLEBAR")


class TitleBar(QtWidgets.QWidget):
    """The merged caption row. Hosts the CommandBar pieces + (frameless mode) window buttons."""

    def __init__(self, win, commandbar, parent=None):
        super().__init__(parent)
        self._win = win
        self.setFixedHeight(TITLEBAR_H)
        # Selector-SCOPED: a bare "background:…" cascades into every descendant — including
        # the QMenuBar's popup QMenus, which would override the unified dropdown surface.
        self.setObjectName("titlebar")
        self.setStyleSheet(f"#titlebar{{background:{theme.BG};}}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 0, 0)
        lay.setSpacing(6)

        brand = QtWidgets.QLabel()
        brand.setPixmap(icons.brand_icon(theme.ACCENT, theme.BG).pixmap(18, 18))
        brand.setFixedSize(22, 22)
        brand.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        lay.addWidget(brand)

        # the existing CommandBar (menu bar + box + launchers) IS the body of the title bar
        commandbar.setFixedHeight(TITLEBAR_H - 2)
        commandbar.setStyleSheet("CommandBar{background:transparent;}")  # scoped — see above
        lay.addWidget(commandbar, 1)

        self._win_buttons: list[QtWidgets.QToolButton] = []
        if frameless_enabled():
            for glyph, tip, slot, danger in (
                ("─", "Minimize", win.showMinimized, False),
                ("□", "Maximize", self._toggle_max, False),
                ("✕", "Close", win.close, True),
            ):
                b = QtWidgets.QToolButton()
                b.setText(glyph)
                b.setToolTip(tip)
                b.setFixedSize(46, TITLEBAR_H)
                b.setCursor(QtCore.Qt.PointingHandCursor)
                hover = "#c42b1c" if danger else theme.PANEL
                b.setStyleSheet(
                    f"QToolButton{{border:none;background:transparent;color:{theme.TEXT2};"
                    f"font-size:13px;}}"
                    f"QToolButton:hover{{background:{hover};color:{theme.TEXT};}}"
                )
                b.clicked.connect(slot)
                lay.addWidget(b)
                self._win_buttons.append(b)
            self._max_btn = self._win_buttons[1]
    def _is_maxed(self) -> bool:
        """Native truth: Qt's isMaximized() desyncs under the frameless+WS_CAPTION combo (the
        live test caught showNormal() no-opping), so ask Win32 directly in frameless mode."""
        if frameless_enabled():
            return bool(ctypes.windll.user32.IsZoomed(
                ctypes.wintypes.HWND(int(self._win.winId()))))
        return self._win.isMaximized()

    def _toggle_max(self) -> None:
        if frameless_enabled():
            # drive the NATIVE maximize/restore — Qt's show* no-ops when its state is stale
            SW_MAXIMIZE, SW_RESTORE = 3, 9
            hwnd = ctypes.wintypes.HWND(int(self._win.winId()))
            ctypes.windll.user32.ShowWindow(
                hwnd, SW_RESTORE if self._is_maxed() else SW_MAXIMIZE)
        elif self._win.isMaximized():
            self._win.showNormal()
        else:
            self._win.showMaximized()
        if self._win_buttons:
            from .unifiedbar import update_max_button_state
            update_max_button_state(self._max_btn, self._is_maxed())

    # Fallback paths (non-frameless platforms / tests): plain Qt drag + double-click maximize.
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == QtCore.Qt.LeftButton and not frameless_enabled():
            handle = self._win.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if e.button() == QtCore.Qt.LeftButton and not frameless_enabled():
            self._toggle_max() if self._win_buttons else None
        super().mouseDoubleClickEvent(e)


class FramelessWindowFilter(QtCore.QAbstractNativeEventFilter):
    """Win32 filter giving a frameless window native caption behavior.

    WM_NCCALCSIZE -> client area covers the whole window (no native caption/frame painted);
    when maximized, inset by the system frame so content isn't pushed off-screen.
    WM_NCHITTEST -> resize codes on an 8px border ring; HTCAPTION over the title bar's
    non-interactive background (so drag / Aero Snap / double-click-maximize are all native);
    HTCLIENT over every interactive child (buttons, the command box, launchers, menus).
    """

    def __init__(self, win, titlebar):
        super().__init__()
        self._win = win
        self._bar = titlebar
        self._hwnd = int(win.winId())

    def nativeEventFilter(self, etype, message):  # noqa: N802
        if etype not in (b"windows_generic_MSG", "windows_generic_MSG"):
            return False, 0
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.hWnd != self._hwnd:
            return False, 0

        if msg.message == _WM_NCCALCSIZE and msg.wParam:
            if self._is_zoomed():
                # maximized: inset by the resize frame, or the content bleeds past the monitor
                pad = (ctypes.windll.user32.GetSystemMetrics(32)      # SM_CXSIZEFRAME
                       + ctypes.windll.user32.GetSystemMetrics(92))   # SM_CXPADDEDBORDER
                rect = ctypes.wintypes.RECT.from_address(msg.lParam)
                rect.left += pad
                rect.top += pad
                rect.right -= pad
                rect.bottom -= pad
            return True, 0

        if msg.message == _WM_NCHITTEST:
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            return True, self._hit_test(x, y)

        return False, 0

    def _is_zoomed(self) -> bool:
        return bool(ctypes.windll.user32.IsZoomed(self._hwnd))

    def _hit_test(self, gx: int, gy: int) -> int:
        # global physical px -> logical window coords
        ratio = self._win.devicePixelRatioF() or 1.0
        top_left = self._win.mapToGlobal(QtCore.QPoint(0, 0))
        lx = gx / ratio - top_left.x()
        ly = gy / ratio - top_left.y()
        w, h = self._win.width(), self._win.height()

        if not self._is_zoomed():
            b = _RESIZE_BORDER
            on_l, on_r = lx < b, lx > w - b
            on_t, on_b = ly < b, ly > h - b
            if on_t and on_l:
                return _HTTOPLEFT
            if on_t and on_r:
                return _HTTOPRIGHT
            if on_b and on_l:
                return _HTBOTTOMLEFT
            if on_b and on_r:
                return _HTBOTTOMRIGHT
            if on_l:
                return _HTLEFT
            if on_r:
                return _HTRIGHT
            if on_t:
                return _HTTOP
            if on_b:
                return _HTBOTTOM

        if 0 <= ly <= TITLEBAR_H:
            # interactive children stay clickable; only the bar's background drags the window
            child = self._win.childAt(QtCore.QPoint(int(lx), int(ly)))
            if child is None:
                return _HTCAPTION
            interactive = isinstance(child, (QtWidgets.QAbstractButton, QtWidgets.QLineEdit,
                                             QtWidgets.QToolBar, QtWidgets.QMenu,
                                             QtWidgets.QMenuBar))
            return _HTCLIENT if interactive else _HTCAPTION
        return _HTCLIENT


def install_frameless(win, titlebar) -> "FramelessWindowFilter | None":
    """Make ``win`` frameless with native move/snap/resize. Returns the installed filter."""
    if not frameless_enabled():
        return None
    win.setWindowFlag(QtCore.Qt.FramelessWindowHint, True)
    # Re-add WS_THICKFRAME (and WS_CAPTION for snap animations): Qt's frameless strips them,
    # but Windows needs them for Aero Snap + native resize; WM_NCCALCSIZE hides their pixels.
    hwnd = int(win.winId())
    GWL_STYLE = -16
    style = ctypes.windll.user32.GetWindowLongPtrW(ctypes.wintypes.HWND(hwnd), GWL_STYLE)
    WS_THICKFRAME, WS_CAPTION = 0x00040000, 0x00C00000
    ctypes.windll.user32.SetWindowLongPtrW(ctypes.wintypes.HWND(hwnd), GWL_STYLE,
                                           style | WS_THICKFRAME | WS_CAPTION)
    flt = FramelessWindowFilter(win, titlebar)
    QtWidgets.QApplication.instance().installNativeEventFilter(flt)
    return flt
