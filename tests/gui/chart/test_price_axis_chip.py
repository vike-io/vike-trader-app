"""Isolated tests for the right-axis last-price chip (PriceAxis.set_last / chip_top).

The chip (#198/#199) was previously verified only by shot.py + the broad chart suite; this pins
the pure paint-math (price->y mapping, off-screen guard, in-band clamp) and the set_last dedup /
None / NaN behaviour so a refactor of the boundingRect/grid assumption can't silently regress it.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402

from vike_trader_app.ui.chart import PriceAxis  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# --- pure paint-math (no Qt instance needed) ----------------------------------------------------

def test_chip_top_maps_price_to_axis_band():
    # right axis: hi -> top, lo -> bottom; rect = (top=0, height=400), th=16
    top_hi = PriceAxis.chip_top(0.0, 400.0, 100.0, 200.0, 200.0, 16.0)   # price == hi
    top_lo = PriceAxis.chip_top(0.0, 400.0, 100.0, 200.0, 100.0, 16.0)   # price == lo
    top_mid = PriceAxis.chip_top(0.0, 400.0, 100.0, 200.0, 150.0, 16.0)  # midpoint
    assert top_hi == 0.0                       # clamped flush to the top edge
    assert top_lo == 400.0 - 16.0              # clamped flush to the bottom edge
    assert top_mid == pytest.approx(200.0 - 8.0)  # centred on y=200 (chip height 16)


def test_chip_top_off_range_and_degenerate_return_none():
    assert PriceAxis.chip_top(0.0, 400.0, 100.0, 200.0, 250.0, 16.0) is None   # above hi
    assert PriceAxis.chip_top(0.0, 400.0, 100.0, 200.0, 50.0, 16.0) is None    # below lo
    assert PriceAxis.chip_top(0.0, 400.0, 200.0, 200.0, 200.0, 16.0) is None   # lo == hi
    assert PriceAxis.chip_top(0.0, 400.0, 300.0, 100.0, 200.0, 16.0) is None   # inverted (hi<lo)


# --- set_last state machine (dedup / None / NaN) ------------------------------------------------

class _CountingAxis(PriceAxis):
    def __init__(self, *a, **k):
        self.n_update = 0                       # set BEFORE super().__init__ (pyqtgraph calls update() in it)
        super().__init__(*a, **k)

    def update(self, *a, **k):  # noqa: A003 - Qt method
        self.n_update += 1
        return super().update(*a, **k)


def test_set_last_dedups_repaints(app):
    ax = _CountingAxis(orientation="right")
    ax.n_update = 0                             # ignore construction-time label updates
    ax.set_last(123.0, "#ff0000")
    assert ax._last is not None and ax._last[0] == 123.0
    assert ax.n_update == 1
    ax.set_last(123.0, "#ff0000")              # identical -> no repaint
    assert ax.n_update == 1
    ax.set_last(124.0, "#ff0000")              # changed price -> repaint
    assert ax.n_update == 2


def test_set_last_none_and_nan_clear(app):
    ax = _CountingAxis(orientation="right")
    ax.set_last(123.0, "#ff0000")
    ax.set_last(None, None)                     # explicit clear
    assert ax._last is None
    ax.set_last(123.0, "#ff0000")
    ax.set_last(float("nan"), "#ff0000")        # NaN -> treated as clear (and dedup-safe)
    assert ax._last is None
