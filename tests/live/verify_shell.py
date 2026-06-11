"""Live check of the S2-S4 shell: top bar, documents-only strip, hamburger File menu."""
import os
import sys
import tempfile

os.environ["VIKE_DISABLE_SESSION"] = "1"
os.environ["VIKE_DISABLE_LIVE"] = "1"

from PySide6 import QtCore, QtWidgets
from vike_trader_app.ui.app import MainWindow

OUT = os.path.join(tempfile.gettempdir(), "vike-shots")
os.makedirs(OUT, exist_ok=True)

app = QtWidgets.QApplication(sys.argv)
win = MainWindow(session_path=None)
win.resize(1380, 860)
win.show()
win.raise_()


def step():
    win._load_symbol("BTCUSDT")
    win._new_chart_document("ETHUSDT", "1h")
    win._new_chart_document("SOLUSDT", "4h")

    def shot1():
        pm = win.grab()
        if pm.width() > 1500:
            pm = pm.scaledToWidth(1500, QtCore.Qt.SmoothTransformation)
        pm.save(os.path.join(OUT, "shell_main.png"))
        # open the hamburger File submenu and grab it
        root = win.topbar.menu_btn.menu()
        file_menu = root.actions()[0].menu()
        root.popup(win.topbar.menu_btn.mapToGlobal(QtCore.QPoint(0, 34)))

        def shot2():
            file_menu.popup(win.topbar.menu_btn.mapToGlobal(QtCore.QPoint(140, 40)))

            def shot3():
                root.grab().save(os.path.join(OUT, "shell_menu_root.png"))
                file_menu.grab().save(os.path.join(OUT, "shell_menu_file.png"))
                sys.stdout.write("SAVED shell_main / menu_root / menu_file\n")
                root.hide(); file_menu.hide()
                app.quit()

            QtCore.QTimer.singleShot(450, shot3)

        QtCore.QTimer.singleShot(450, shot2)

    QtCore.QTimer.singleShot(900, shot1)


QtCore.QTimer.singleShot(900, step)
sys.exit(app.exec())
