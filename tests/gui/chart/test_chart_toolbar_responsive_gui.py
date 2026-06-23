"""The chart top toolbar collapses progressively as the chart narrows (multi-chart tiling),
so labels never clip mid-word. Drives _top_bar width directly + asserts via isHidden(), so it
holds offscreen (where isVisible() is always False on an un-shown widget)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.chart import PriceChart  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _at(chart, width):
    chart._top_bar.resize(width, 28)
    chart._relayout_toolbar()


def test_full_toolbar_when_wide(app):
    c = PriceChart()
    _at(c, 800)
    assert not c._range_w.isHidden()          # range selector shown
    assert not c._ohlc_label.isHidden()       # OHLC legend shown
    assert c._ind_btn.text() == "ƒx Indicators"
    c.deleteLater()


def test_progressive_collapse(app):
    c = PriceChart()
    _at(c, 600)                               # < 620: range drops first
    assert c._range_w.isHidden()
    assert not c._ohlc_label.isHidden()
    _at(c, 450)                               # < 470: legend (+ its divider) drops too
    assert c._ohlc_label.isHidden() and c._ohlc_divider.isHidden()
    assert c._ind_btn.text() == "ƒx Indicators"
    _at(c, 340)                               # < 360: shorten the Indicators label
    assert c._ind_btn.text() == "ƒx"
    c.deleteLater()


def test_essentials_always_present(app):
    c = PriceChart()
    _at(c, 200)                               # very narrow
    # symbol label was removed from the toolbar (it lives in the window title bar now)
    assert not c._tf_btn.isHidden()
    assert not c._ind_btn.isHidden()          # collapsed to "ƒx" but still there
    assert not c._style_btn.isHidden()
    c.deleteLater()


def test_re_expands(app):
    c = PriceChart()
    _at(c, 340)
    _at(c, 800)                               # widen back -> everything returns
    assert not c._range_w.isHidden()
    assert not c._ohlc_label.isHidden()
    assert c._ind_btn.text() == "ƒx Indicators"
    c.deleteLater()
