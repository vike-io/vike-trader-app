"""LIVE drive of every S7 feature on the real display: floating chart windows w/ title bars,
no tab strip, arrange grid/cascade, roll-up, maximize, detach + topmost pin, link dots in the
toolbar, command-box routing, menus. Saves staged screenshots + prints objective checks."""
import ctypes
import os
import sys
import tempfile

os.environ["VIKE_DISABLE_SESSION"] = "1"
os.environ["VIKE_DISABLE_LIVE"] = "1"

from PySide6 import QtCore, QtWidgets
from vike_trader_app.ui.app import MainWindow

OUT = os.path.join(tempfile.gettempdir(), "vike-shots")
os.makedirs(OUT, exist_ok=True)
R = []

app = QtWidgets.QApplication(sys.argv)
win = MainWindow(session_path=None)
win.resize(1400, 880)
win.show()
win.raise_()


def grab(name):
    pm = win.grab()
    if pm.width() > 1500:
        pm = pm.scaledToWidth(1500, QtCore.Qt.SmoothTransformation)
    pm.save(os.path.join(OUT, name))


def step1():
    win._load_symbol("BTCUSDT")
    for s, iv in (("ETHUSDT", "1h"), ("SOLUSDT", "4h"), ("ADAUSDT", "1h")):
        win._new_chart_document(s, iv)
    QtCore.QTimer.singleShot(700, step2)


def step2():
    R.append(("3 floating windows", len(win._chart_frames) == 3))
    R.append(("strip hidden", not win.tabs._resolve_area().titleBar().isVisible()))
    f = win._chart_frames[0]
    R.append(("title bar text", f._title.text() == "ETHUSDT · 1h"))
    d = win._doc_widgets[0]
    R.append(("link dots IN toolbar", d._link_dot.parent() is d.chart._top_bar))
    grab("s7_floating.png")
    win._arrange_chart_windows("grid")
    QtCore.QTimer.singleShot(500, step3)


def step3():
    geos = [fr.geometry() for fr in win._chart_frames]
    ok = all(not geos[i].intersects(geos[j]) for i in range(3) for j in range(i + 1, 3))
    R.append(("grid: no overlap", ok))
    grab("s7_grid.png")
    win._arrange_chart_windows("cascade")
    QtCore.QTimer.singleShot(400, step4)


def step4():
    grab("s7_cascade.png")
    f = win._chart_frames[1]
    f.toggle_rollup()
    R.append(("roll-up height", f.height() <= 40))
    f.toggle_rollup()
    f.toggle_max()
    R.append(("maximize fills workspace", f.size() == win.dock_manager.size()))
    f.toggle_max()
    # detach + topmost pin (native check)
    f2 = win._chart_frames[2]
    f2.toggle_detach()
    QtCore.QTimer.singleShot(500, lambda: step5(f2))


def step5(f2):
    R.append(("detached = own OS window", f2.is_detached() and f2.isWindow()))
    doc = f2.doc
    R.append(("pin visible when detached", not doc._pin_btn.isHidden()))
    doc._pin_btn.setChecked(True)
    QtWidgets.QApplication.processEvents()
    GWL_EXSTYLE, WS_EX_TOPMOST = -20, 0x00000008
    ex = ctypes.windll.user32.GetWindowLongPtrW(
        ctypes.c_void_p(int(f2.winId())), GWL_EXSTYLE)
    R.append(("pin -> native TOPMOST", bool(ex & WS_EX_TOPMOST)))
    doc._pin_btn.setChecked(False)
    f2.toggle_detach()           # re-attach
    QtWidgets.QApplication.processEvents()
    R.append(("re-attached", not f2.is_detached()))
    # command box routes to the ACTIVE frame
    win._on_chart_window_activated(win._chart_frames[0])
    win.topbar.box.setText("XRPUSDT")
    win.topbar._submit()
    R.append(("box -> active window", win._chart_frames[0].doc.symbol == "XRPUSDT"))
    grab("s7_final.png")
    for k, v in R:
        sys.stdout.write(f"{'PASS' if v else 'FAIL'}  {k}\n")
    sys.stdout.write("LIVE S7 " + ("ALL PASS\n" if all(v for _k, v in R) else "HAS FAILURES\n"))
    app.quit()


QtCore.QTimer.singleShot(1100, step1)
sys.exit(app.exec())
