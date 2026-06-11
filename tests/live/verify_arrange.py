"""Live verification: Arrange grid + keep-on-top pin (worktree build).

Launches the real MainWindow on the real display, opens 4 chart documents,
tiles them via arrange_documents('grid'), grabs a PNG; then floats one chart,
pins it, and reads back the Win32 WS_EX_TOPMOST style bit (objective check).
"""
import ctypes
import os
import sys

from PySide6 import QtCore, QtWidgets

TMP = os.environ.get("TEMP", ".")
OUT_GRID = os.path.join(TMP, "vike_arrange_grid.png")
OUT_PIN = os.path.join(TMP, "vike_pin_float.png")

from vike_trader_app.ui.app import MainWindow  # noqa: E402


def topmost_bit(hwnd: int) -> bool:
    GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x00000008
    return bool(ctypes.windll.user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST)


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.resize(1480, 900)
    win.show()

    def guarded(fn):
        def run():
            try:
                fn()
            except Exception:
                import traceback
                traceback.print_exc()
                print("FAILED in", fn.__name__, flush=True)
                app.quit()
        return run

    def open_and_tile():
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "ADAUSDT"):
            win._new_chart_document(sym, "1h")
        n = win.tabs.arrange_documents("grid")
        print(f"MEASURE arranged={n}", flush=True)
        areas = {id(d.dockAreaWidget()) for d in win.tabs._documents}
        print(f"MEASURE distinct_areas={len(areas)}", flush=True)

    def grab_grid():
        win.grab().save(OUT_GRID)
        print(f"SAVED {OUT_GRID}", flush=True)
        dock = win.tabs._documents[0]
        dock.setFloating()
        QtCore.QTimer.singleShot(900, guarded(pin_and_check))

    def pin_and_check():
        doc = win.tabs._documents[0].widget()
        w = doc.window()
        print(f"MEASURE float_window={type(w).__name__}", flush=True)
        print(f"MEASURE pin_visible={not doc._pin_btn.isHidden()}", flush=True)
        hwnd = int(w.winId())
        print(f"MEASURE topmost_before={topmost_bit(hwnd)}", flush=True)
        doc._pin_btn.setChecked(True)
        print(f"MEASURE topmost_after_pin={topmost_bit(hwnd)}", flush=True)
        doc._pin_btn.setChecked(False)
        print(f"MEASURE topmost_after_unpin={topmost_bit(hwnd)}", flush=True)
        doc._pin_btn.setChecked(True)   # leave pinned for the grab
        w.grab().save(OUT_PIN)
        print(f"SAVED {OUT_PIN}", flush=True)
        QtCore.QTimer.singleShot(300, app.quit)

    QtCore.QTimer.singleShot(2500, guarded(open_and_tile))
    QtCore.QTimer.singleShot(9000, guarded(grab_grid))
    QtCore.QTimer.singleShot(45000, app.quit)   # hard failsafe — never hang
    app.exec()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
