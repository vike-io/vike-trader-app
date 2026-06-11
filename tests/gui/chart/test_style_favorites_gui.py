"""TradingView-style chart-type favorites: star the active style -> a one-click icon button
pinned next to the selector; persisted via QSettings; unstar removes it."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.ui.chart import PriceChart  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def _tmp_settings(tmp_path, monkeypatch):
    """Point the favorites store at a throwaway ini so tests never touch real QSettings."""
    path = str(tmp_path / "favs.ini")
    monkeypatch.setattr(
        PriceChart, "_settings",
        staticmethod(lambda: QtCore.QSettings(path, QtCore.QSettings.IniFormat)))


def _fav_buttons(pc):
    lay = pc._fav_bar.layout()
    return [lay.itemAt(i).widget() for i in range(lay.count())]


def test_star_pins_unstar_removes(app, _tmp_settings):
    pc = PriceChart()
    pc._top_bar.resize(800, 28)
    assert pc._fav_styles == []
    pc.set_style("Renko")
    pc._toggle_style_favorite()                     # star Renko
    assert pc._fav_styles == ["Renko"]
    btns = _fav_buttons(pc)
    assert len(btns) == 1 and btns[0].toolTip() == "Renko"
    pc._toggle_style_favorite()                     # unstar
    assert pc._fav_styles == []
    pc.deleteLater()


def test_fav_button_switches_style(app, _tmp_settings):
    pc = PriceChart()
    pc._top_bar.resize(800, 28)
    pc.set_style("Kagi")
    pc._toggle_style_favorite()
    pc.set_style("Candles")
    _fav_buttons(pc)[0].click()                     # the pinned Kagi button
    assert pc._style == "Kagi"
    pc.deleteLater()


def test_favorites_persist_across_charts(app, _tmp_settings):
    pc = PriceChart()
    pc.set_style("Area")
    pc._toggle_style_favorite()
    pc.deleteLater()
    pc2 = PriceChart()                               # fresh chart -> loads from settings
    assert pc2._fav_styles == ["Area"]
    pc2.deleteLater()


def test_fav_menu_label_reflects_state(app, _tmp_settings):
    pc = PriceChart()
    pc.set_style("Bars")
    pc._refresh_fav_action()
    assert pc._fav_action.text() == "★ Favorite Bars"
    pc._toggle_style_favorite()
    pc._refresh_fav_action()
    assert pc._fav_action.text() == "★ Unfavorite Bars"
    pc.deleteLater()


def test_fav_bar_hides_when_narrow(app, _tmp_settings):
    pc = PriceChart()
    pc.set_style("Line")
    pc._toggle_style_favorite()
    pc._top_bar.resize(800, 28)
    pc._relayout_toolbar()
    assert not pc._fav_bar.isHidden()
    pc._top_bar.resize(400, 28)
    pc._relayout_toolbar()
    assert pc._fav_bar.isHidden()
    pc.deleteLater()
