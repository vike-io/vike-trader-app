"""Live check: icon-only style button + the dropdown with per-style glyphs (real display)."""
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
win.resize(1280, 800)
win.show()
win.raise_()


def step():
    win._load_symbol("BTCUSDT")

    def shots():
        menu = win.price._style_btn.menu()
        menu.popup(win.price._style_btn.mapToGlobal(QtCore.QPoint(0, 24)))

        def grab():
            # toolbar strip (top of the chart) + the open menu, separately
            bar = win.price._top_bar.grab()
            bar.save(os.path.join(OUT, "style_toolbar.png"))
            menu.grab().save(os.path.join(OUT, "style_menu.png"))
            menu.hide()
            sys.stdout.write("SAVED toolbar+menu\n")
            app.quit()

        QtCore.QTimer.singleShot(600, grab)

    QtCore.QTimer.singleShot(1200, shots)


QtCore.QTimer.singleShot(800, step)
sys.exit(app.exec())
