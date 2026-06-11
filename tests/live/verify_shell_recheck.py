"""Post-merge LIVE recheck of the whole shell program on main: drives every shipped feature,
prints objective state, saves (1) native-chrome top region (PrintWindow incl. OS title bar),
(2) full window, (3) the File menu."""
import ctypes
import os
import sys
import tempfile
from ctypes import wintypes

os.environ["VIKE_DISABLE_SESSION"] = "1"
os.environ["VIKE_DISABLE_LIVE"] = "1"

from PySide6 import QtCore, QtGui, QtWidgets
from vike_trader_app.ui.app import MainWindow

OUT = os.path.join(tempfile.gettempdir(), "vike-shots")
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
win = MainWindow(session_path=None)
win.resize(1380, 860)
win.show()
win.raise_()
REPORT = []


def printwindow_png(path, crop_h=None):
    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]
    hwnd = int(win.winId())
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    wdc = u.GetWindowDC(hwnd)
    mdc = g.CreateCompatibleDC(wdc)
    bmp = g.CreateCompatibleBitmap(wdc, w, h)
    g.SelectObject(mdc, bmp)
    u.PrintWindow(hwnd, mdc, 2)
    bmi = BMIH(); bmi.biSize = ctypes.sizeof(BMIH)
    bmi.biWidth, bmi.biHeight = w, -h
    bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    img = QtGui.QImage(bytes(buf), w, h, QtGui.QImage.Format_RGB32)
    if crop_h:
        img = img.copy(0, 0, w, crop_h)
    img.save(path)
    g.DeleteObject(bmp); g.DeleteDC(mdc); u.ReleaseDC(hwnd, wdc)


def step1():
    win._load_symbol("BTCUSDT")
    # S2: command box routing
    win.topbar.box.setText("ETHUSDT"); win.topbar._submit()
    REPORT.append(f"box ETHUSDT -> symbol={win._symbol}")
    win.topbar.box.setText("5m"); win.topbar._submit()
    REPORT.append(f"box 5m      -> interval={win._interval}")
    # docs + strip
    win._new_chart_document("SOLUSDT", "4h")
    QtCore.QTimer.singleShot(700, step2)


def step2():
    hidden = all(not d.tabWidget().isVisible() for d in win.tabs._docks)
    REPORT.append(f"space tabs hidden={hidden}; doc tabs={[d.widget().title() for d in win.tabs._documents]}")
    # S4: copy/paste window
    win._copy_active_document(); win._paste_document()
    REPORT.append(f"copy/paste -> docs={win.tabs.document_count()}")
    # recents
    win._apply_workspace("Research")
    REPORT.append(f"recents={win._workspaces.recents()}")
    QtCore.QTimer.singleShot(700, step3)


def step3():
    printwindow_png(os.path.join(OUT, "recheck_titlebar.png"), crop_h=170)
    pm = win.grab()
    if pm.width() > 1500:
        pm = pm.scaledToWidth(1500, QtCore.Qt.SmoothTransformation)
    pm.save(os.path.join(OUT, "recheck_full.png"))
    root = win.topbar.menu_btn.menu()
    fm = root.actions()[0].menu()
    fm.popup(win.topbar.menu_btn.mapToGlobal(QtCore.QPoint(40, 40)))

    def fin():
        fm.grab().save(os.path.join(OUT, "recheck_filemenu.png"))
        fm.hide()
        for line in REPORT:
            sys.stdout.write(line + "\n")
        sys.stdout.write("RECHECK DONE\n")
        app.quit()

    QtCore.QTimer.singleShot(500, fin)


QtCore.QTimer.singleShot(1000, step1)
sys.exit(app.exec())
