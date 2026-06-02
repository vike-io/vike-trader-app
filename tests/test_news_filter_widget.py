"""TV-style multi-select filter dropdown (MultiSelectFilter): selection, label, search, select-all."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.news_filter import MultiSelectFilter  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_selection_and_button_text(app):
    f = MultiSelectFilter("Provider", ["Reuters", "CNBC", "CoinDesk"])
    assert f.selected() == set()
    assert f.text().startswith("Provider") and "(" not in f.text()   # no count when empty
    f.set_selected({"CNBC", "Reuters"})
    assert f.selected() == {"CNBC", "Reuters"}
    assert "(2)" in f.text()


def test_set_selected_emits_signal(app):
    f = MultiSelectFilter("Market", ["Crypto", "Forex", "Stocks"])
    seen = []
    f.selectionChanged.connect(lambda: seen.append(True))
    f.set_selected({"Crypto"})
    assert seen and f.selected() == {"Crypto"}


def test_select_all_toggles(app):
    f = MultiSelectFilter("Market", ["Crypto", "Forex", "Stocks"])
    f._pop._on_select_all_clicked()                # check all
    assert f.selected() == {"Crypto", "Forex", "Stocks"}
    f._pop._on_select_all_clicked()                # uncheck all
    assert f.selected() == set()


def test_search_hides_nonmatching_rows(app):
    f = MultiSelectFilter("Provider", ["Reuters", "CNBC", "CoinDesk"])
    f._pop._apply_filter("coin")
    assert not f._pop._boxes["CoinDesk"].isHidden()
    assert f._pop._boxes["Reuters"].isHidden()
    f._pop._apply_filter("")                       # clearing shows all again
    assert not f._pop._boxes["Reuters"].isHidden()
