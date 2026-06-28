"""Regression guard: screener scan() fans out via read_series_many (parallel reads)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.screener import ScreenerTab  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Cat:
    def __init__(self, syms):
        self._syms = syms

    def symbols(self):
        return list(self._syms)

    def intervals(self, symbol):
        return ["1m"]

    def query(self, symbol, interval, start=None, end=None):
        # rising series so a rule produces a signal
        return [Bar(ts=i * 60_000, open=1.0 + i, high=1.0 + i, low=1.0 + i,
                    close=1.0 + i, volume=100.0) for i in range(30)]


def test_scan_fills_table_via_parallel_reads(app):
    tab = ScreenerTab()
    tab._catalog = lambda: _Cat(["AAA", "BBB", "CCC"])   # override the collaborator seam
    tab._populate_intervals()
    tab.scan()
    assert tab._table.rowCount() == 3                     # all three symbols scanned + filled
