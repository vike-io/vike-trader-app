"""Live verification: dashboard tiles on the real display — open all four, grab a PNG."""
import os
import sys

from PySide6 import QtCore, QtWidgets

TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "vike_tiles.png")

from vike_trader_app.ui.app import MainWindow  # noqa: E402


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.resize(1480, 900)
    win.show()

    def open_tiles():
        try:
            win.tabs.setCurrentIndex(0)   # panels live on the Chart SPACE (by design)
            for key in ("movers", "pnl", "ecal", "headlines"):
                win._panel_btns[key].setChecked(True)
            print("MEASURE tiles_open=" + ",".join(
                k for k in ("movers", "pnl", "ecal", "headlines")
                if not win._panel_dock_map[k].isClosed()), flush=True)
        except Exception:
            import traceback
            traceback.print_exc()
            app.quit()

    def grab():
        try:
            print(f"MEASURE movers_rows={len(win._movers_tile._rows)}", flush=True)
            print(f"MEASURE ecal_rows={len(win._ecal_tile._rows)}", flush=True)
            print(f"MEASURE news_rows={len(win._headlines_tile._rows)}", flush=True)
            for key in ("movers", "pnl", "ecal", "headlines"):
                d = win._panel_dock_map[key]
                a = d.dockAreaWidget()
                print(f"MEASURE {key}: closed={d.isClosed()} "
                      f"area_w={a.width() if a else None} "
                      f"area_visible={a.isVisible() if a else None} "
                      f"floating={d.isFloating()}", flush=True)
            win.grab().save(OUT)
            print(f"SAVED {OUT}", flush=True)
            for key in ("movers", "headlines"):
                w = win._panel_dock_map[key].widget().window()
                p = os.path.join(TMP, f"vike_tile_{key}.png")
                w.grab().save(p)
                print(f"SAVED {p}", flush=True)
        finally:
            app.quit()

    QtCore.QTimer.singleShot(3000, open_tiles)
    QtCore.QTimer.singleShot(12000, grab)   # give quotes + news a beat to fill
    QtCore.QTimer.singleShot(40000, app.quit)
    app.exec()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
