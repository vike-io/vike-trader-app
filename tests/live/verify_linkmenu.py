"""Live verification: the MultiCharts-style link colour menu on a chart document."""
import os
import sys

from PySide6 import QtCore, QtWidgets

TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "vike_linkmenu.png")

from vike_trader_app.ui.app import MainWindow  # noqa: E402


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.resize(1480, 900)
    win.show()

    def open_menu():
        try:
            doc = (win._doc_widgets or [win._new_chart_document("BTCUSDT", "1h")])[0]
            doc._link_dot.set_group(13)            # pink active -> shows the check
            menu = doc._link_dot.menu()
            menu.popup(doc._link_dot.mapToGlobal(QtCore.QPoint(0, 22)))

            def grab():
                menu.grab().save(OUT)
                print(f"SAVED {OUT}", flush=True)
                menu.close()
                app.quit()
            QtCore.QTimer.singleShot(700, grab)
        except Exception:
            import traceback
            traceback.print_exc()
            app.quit()

    QtCore.QTimer.singleShot(3000, open_menu)
    QtCore.QTimer.singleShot(30000, app.quit)
    app.exec()
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
