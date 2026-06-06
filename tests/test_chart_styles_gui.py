"""Offscreen tests for the chart-style switch (candles/line/Heikin-Ashi/Renko/Kagi/P&F …)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui import chart_styles  # noqa: E402
from vike_trader_app.ui.chart import PriceChart  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=40):
    out = []
    for i in range(n):
        c = 100.0 + i * 0.7  # trending, so Renko/Kagi/P&F produce units
        out.append(Bar(ts=i * 60_000, open=c - 0.3, high=c + 0.5, low=c - 0.5, close=c, volume=1.0 + i))
    return out


def _chart(n=40):
    pc = PriceChart()
    pc.set_data(_bars(n), [])
    return pc


def test_default_is_candles(app):
    pc = _chart()
    assert pc._style == "Candles"
    assert pc._candles.isVisible()


def test_line_style_hides_candles_and_shows_line(app):
    pc = _chart()
    pc.set_style("Line")
    assert not pc._candles.isVisible()
    assert pc._style_items[chart_styles.family("Line")].isVisible()
    assert pc._panes_hidden is False                  # 1:1 -> overlays/markers/panes stay live


def test_heikin_ashi_smooths_but_keeps_1to1(app):
    pc = _chart()
    pc.set_style("Heikin Ashi")
    assert pc._candles.isVisible()                    # HA reuses the candle item
    assert len(pc._shown) == len(pc._bars)            # same count -> indices still align
    assert pc._shown[-1].close != pc._bars[-1].close  # but values are the HA transform
    assert pc._panes_hidden is False


def test_renko_is_nontime_and_hides_overlays(app):
    pc = _chart()
    pc.set_style("Renko")
    assert pc._panes_hidden is True
    assert not pc._candles.isVisible()
    assert pc._style_items[chart_styles.family("Renko")].isVisible()
    assert len(pc._shown) >= 1
    assert len(pc._shown) != len(pc._bars)            # different count = non-time series
    assert not pc._marker_scatter.isVisible()         # markers hidden on non-time styles


def test_switch_back_to_candles_restores_overlays(app):
    pc = _chart()
    pc.set_style("Renko")
    pc.set_style("Candles")
    assert pc._panes_hidden is False
    assert pc._candles.isVisible()
    assert pc._marker_scatter.isVisible()


def test_kagi_and_pnf_build_structures(app):
    pc = _chart()
    pc.set_style("Kagi")
    assert pc._kagi_res is not None
    assert pc._style_items["kagi"].isVisible()
    pc.set_style("Point & Figure")
    assert pc._pnf_res is not None
    assert pc._style_items["pnf"].isVisible()


def test_every_style_switches_without_error(app):
    pc = _chart()
    for s in chart_styles.ALL_STYLES:
        pc.set_style(s)
        assert pc._style == s
        assert pc._style_btn.text() == s
    pc.set_style("Candles")
    assert pc._candles.isVisible()


def test_unknown_style_is_ignored(app):
    pc = _chart()
    pc.set_style("Bogus")
    assert pc._style == "Candles"


def test_oscillator_pane_hidden_on_nontime_then_restored(app):
    from PySide6 import QtCore
    pc = PriceChart()
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.addWidget(pc)
    pc.set_pane_host(split)
    pc.set_data(_bars(), [])
    pc.add_indicator("rsi")                 # creates an oscillator pane (split now has 2 widgets)
    assert split.count() == 2
    pc.set_style("Renko")
    assert pc._panes_hidden is True         # pane hidden, not destroyed
    assert split.count() == 2
    pc.set_style("Candles")
    assert pc._panes_hidden is False
    assert split.count() == 2


def _chart_with_host():
    from PySide6 import QtCore
    pc = PriceChart()
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.addWidget(pc)
    pc.set_pane_host(split)
    pc.set_data(_bars(), [])
    return pc, split


def test_overlay_stays_hidden_when_toggled_while_nontime(app):
    pc, _ = _chart_with_host()
    ind = pc.add_indicator("ema")           # a price overlay
    pc.set_style("Renko")
    curve = next(iter(pc._indicators[ind.uid].curves.values()))
    assert not curve.isVisible()
    # toggling the indicator off/on while on Renko must NOT re-show it on the synthetic axis
    pc.set_indicator_visible(ind.uid, False)
    pc.set_indicator_visible(ind.uid, True)
    assert not curve.isVisible()
    pc.set_style("Candles")                 # back to a time style -> restored
    curve = next(iter(pc._indicators[ind.uid].curves.values()))
    assert curve.isVisible()


def test_new_oscillator_added_while_nontime_is_hidden(app):
    pc, split = _chart_with_host()
    pc.set_style("Renko")
    ind = pc.add_indicator("rsi")           # a NEW oscillator pane created WHILE on Renko
    assert ind.pane is not None
    assert ind.pane.isHidden()              # must not pop into view on the synthetic axis
    assert split.count() == 2
    pc.set_style("Candles")
    assert not ind.pane.isHidden()          # restored on switch back
