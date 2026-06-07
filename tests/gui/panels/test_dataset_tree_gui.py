# tests/test_dataset_tree_gui.py
"""Offscreen tests for the DataSets tree (left pane)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.data.datasets import DataSet, save_dataset  # noqa: E402
from vike_trader_app.ui.dataset_tree import DataSetTree  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_tree_groups_datasets_under_provider_nodes(app, tmp_path):
    save_dataset(DataSet("Crypto Majors", ["BTCUSDT"], provider="binance"), str(tmp_path))
    save_dataset(DataSet("FX Majors", ["EURUSD"], provider="dukascopy"), str(tmp_path))
    save_dataset(DataSet("My Mix", []), str(tmp_path))  # unlinked + empty -> My DataSets only
    tree = DataSetTree(str(tmp_path))
    tree.reload()
    assert tree.node_names("All") == ["Crypto Majors", "FX Majors", "My Mix"]
    assert tree.node_names("Binance") == ["Crypto Majors"]
    assert tree.node_names("Dukascopy") == ["FX Majors"]
    assert "My Mix" in tree.node_names("My DataSets")


def test_new_dataset_creates_and_emits(app, tmp_path):
    tree = DataSetTree(str(tmp_path))
    tree.reload()
    seen = []
    tree.dataset_selected.connect(seen.append)
    tree.create_dataset("Fresh")           # dialog-free
    from vike_trader_app.data.datasets import load_dataset
    assert load_dataset("Fresh", str(tmp_path)) is not None
    assert "Fresh" in tree.node_names("All")
    assert seen and seen[-1] == "Fresh"


def test_tree_has_min_width_so_names_dont_elide(app, tmp_path):
    # Regression (live-QA): nested DataSet names elided to an ambiguous "Crypto …" — needs min width.
    tree = DataSetTree(str(tmp_path))
    assert tree._tree.minimumWidth() >= 180
