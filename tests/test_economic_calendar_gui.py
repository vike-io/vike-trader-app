# tests/test_economic_calendar_gui.py
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")

from PySide6 import QtWidgets, QtGui
from vike_trader_app.ui.calendar_delegate import importance_bar_pixmap, value_color


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_importance_pixmap_sizes(app):
    pm = importance_bar_pixmap(2)
    assert isinstance(pm, QtGui.QPixmap) and not pm.isNull()


def test_value_color_beat_miss(app):
    from vike_trader_app.ui import theme
    assert value_color(actual=3.5, forecast=3.2) == theme.UP     # beat
    assert value_color(actual=3.0, forecast=3.2) == theme.DOWN   # miss
    assert value_color(actual=3.2, forecast=3.2) == theme.TEXT   # inline
    assert value_color(actual=None, forecast=3.2) == theme.TEXT  # unreleased
