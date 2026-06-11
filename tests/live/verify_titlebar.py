"""LIVE check of the S6 custom title bar: frameless, one merged caption row, working buttons."""
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
    return w, h


def step():
    win._load_symbol("BTCUSDT")
    win._new_chart_document("ETHUSDT", "1h")

    def checks():
        fg, geo = win.frameGeometry(), win.geometry()
        caption_h = geo.y() - fg.y()
        print(f"frame={fg.width()}x{fg.height()} client={geo.width()}x{geo.height()} "
              f"native-caption-height={caption_h} (0 = frameless OK)")
        w, h = printwindow_png(os.path.join(OUT, "titlebar_live.png"), crop_h=150)
        print(f"printwindow {w}x{h} saved")
        # maximize via OUR button, then restore
        win.titlebar._toggle_max()

        def after_max():
            print("maximized:", win.isMaximized())
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            print(f"fills available screen: {win.geometry().width() >= scr.width() - 4}")
            printwindow_png(os.path.join(OUT, "titlebar_max.png"), crop_h=150)
            win.titlebar._toggle_max()

            def done():
                print("restored:", not win.isMaximized())
                print("LIVE S6 OK")
                app.quit()

            QtCore.QTimer.singleShot(500, done)

        QtCore.QTimer.singleShot(600, after_max)

    QtCore.QTimer.singleShot(1200, checks)


QtCore.QTimer.singleShot(900, step)
sys.exit(app.exec())
