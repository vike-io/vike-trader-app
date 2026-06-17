"""Frameless chart-window cursor behaviour (VS/MetaTrader-style): the edge-resize cursor must stay
on the 6px border and NEVER bleed into the title bar / chart content, and must not stick after a
resize drag ends off-edge."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("VIKE_DISABLE_LIVE", "1")

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("PySide6QtAds")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from vike_trader_app.ui.chartdoc import ChartDocument  # noqa: E402
from vike_trader_app.ui.chartwin import ChartWindowFrame  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _frame(app):
    host = QtWidgets.QWidget()
    host.resize(1200, 800)
    f = ChartWindowFrame(ChartDocument("BTCUSDT", "1m"), host)
    f.resize(600, 400)
    return host, f


def test_content_does_not_inherit_the_resize_cursor(app):
    """Even with the frame showing a resize cursor (mid-drag), the title bar + body keep THEIR own
    cursor — so it can't stick over the chart/title (the reported bug)."""
    _host, f = _frame(app)
    f.setCursor(QtCore.Qt.SizeHorCursor)            # as a width-resize drag sets it
    assert f.doc.cursor().shape() == QtCore.Qt.ArrowCursor
    assert f._bar.cursor().shape() == QtCore.Qt.ArrowCursor


def test_edge_detection_corners_vs_middle(app):
    _host, f = _frame(app)
    w, h = f.width(), f.height()
    assert f._edge_at(QtCore.QPoint(1, 1)) == (True, True, False, False)          # top-left
    assert f._edge_at(QtCore.QPoint(w - 1, h - 1)) == (False, False, True, True)  # bottom-right
    assert f._edge_at(QtCore.QPoint(w // 2, h // 2)) == (False, False, False, False)  # middle: none


def test_resize_cursor_cleared_on_release_off_edge(app):
    """Ending a resize drag with the pointer off any edge must drop the ↔/↕ cursor back to arrow."""
    _host, f = _frame(app)
    f.setCursor(QtCore.Qt.SizeVerCursor)
    f._resize_edge = (False, True, False, False)    # pretend a top-edge resize was underway
    mid = QtCore.QPointF(f.width() / 2, f.height() / 2)
    ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonRelease, mid,
                           QtCore.Qt.LeftButton, QtCore.Qt.NoButton, QtCore.Qt.NoModifier)
    f.mouseReleaseEvent(ev)
    assert f.cursor().shape() == QtCore.Qt.ArrowCursor
