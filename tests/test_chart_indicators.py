"""Offscreen tests for the indicator lifecycle on the price chart: adding, toggling/editing,
and deleting indicators across all four routes — price overlays, stacked oscillator panes,
candlestick pattern markers, and pairs (vs a 2nd-symbol benchmark) — plus the category picker.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.chart import (  # noqa: E402
    _PICKER_TABS,
    _IndicatorPicker,
    OscillatorPane,
    PriceChart,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=80):
    """Varying OHLC so warm-up-gated indicators (RSI, MACD, …) produce real values."""
    out = []
    for i in range(n):
        c = 100 + (i % 9) - 4 + i * 0.15  # zig-zag with a slow uptrend
        out.append(Bar(ts=i * 60_000, open=c - 0.5, high=c + 1.2, low=c - 1.1, close=c))
    return out


def _chart_with_host(app):
    """A PriceChart wired to a vertical splitter pane-host (as app.py mounts it)."""
    pc = PriceChart()
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.addWidget(pc)
    pc.set_pane_host(split)
    pc.set_data(_bars(), [])
    return pc, split


# --- ADD: price overlays -------------------------------------------------------------------
def test_add_overlay_goes_on_price_not_a_pane(app):
    pc, split = _chart_with_host(app)
    pc.add_indicator("ema")
    assert "ema" in pc._overlays and "ema" in pc._overlay_curves
    assert not pc._osc_panes          # overlays do NOT create a pane
    assert split.count() == 1


def test_add_multiple_overlays_coexist(app):
    pc, _ = _chart_with_host(app)
    pc.add_indicator("ema")
    pc.add_indicator("sma")
    assert {"ema", "sma"} <= set(pc._overlays)


def test_multi_output_overlay_splits_into_named_lines(app):
    pc, _ = _chart_with_host(app)
    pc.add_indicator("bollinger")  # bands -> several outputs, each its own overlay line
    assert any(k.startswith("bollinger") for k in pc._overlays)
    assert len(pc._overlay_curves) >= 2


# --- ADD / DELETE: oscillator panes --------------------------------------------------------
def test_add_oscillator_creates_stacked_pane(app):
    pc, split = _chart_with_host(app)
    pc.add_indicator("rsi")
    assert "rsi" in pc._osc_panes
    assert isinstance(pc._osc_panes["rsi"], OscillatorPane)
    assert split.count() == 2          # price chart + 1 oscillator pane
    assert "rsi" not in pc._overlays   # not on the price scale


def test_multiple_oscillators_stack(app):
    pc, split = _chart_with_host(app)
    pc.add_indicator("rsi")
    pc.add_indicator("macd")
    assert set(pc._osc_panes) == {"rsi", "macd"}
    assert split.count() == 3


def test_add_oscillator_is_idempotent(app):
    pc, split = _chart_with_host(app)
    pc.add_indicator("rsi")
    pc.add_indicator("rsi")            # adding the same one again is a no-op
    assert list(pc._osc_panes) == ["rsi"]
    assert split.count() == 2


def test_delete_oscillator_via_close_signal(app):
    pc, split = _chart_with_host(app)
    pc.add_indicator("rsi")
    pane = pc._osc_panes["rsi"]
    pane.paneClosed.emit(pane)         # the pane's ✕ button emits this
    assert "rsi" not in pc._osc_panes
    assert split.count() == 1


def test_oscillator_pane_xlinked_to_price(app):
    pc, _ = _chart_with_host(app)
    pc.add_indicator("rsi")
    pane = pc._osc_panes["rsi"]
    linked = pane.getViewBox().linkedView(pane.getViewBox().XAxis)
    assert linked is pc.getViewBox()   # follows the price chart's x-range


def test_oscillator_reveals_in_lockstep(app):
    pc, _ = _chart_with_host(app)
    pc.add_indicator("rsi")
    pane = pc._osc_panes["rsi"]

    def _points(p):  # total plotted points across the pane's curves (None when empty)
        return sum(0 if (xs := c.getData()[0]) is None else len(xs) for c in p._curves.values())

    pane.show_upto(5)        # within RSI warm-up -> nothing revealed yet
    early = _points(pane)
    pane.show_upto(75)       # well past warm-up -> points revealed
    late = _points(pane)
    assert late > early


# --- ADD / TOGGLE (edit) / DELETE: candlestick patterns ------------------------------------
def test_add_pattern_registers_marker_layer(app):
    pc, _ = _chart_with_host(app)
    pc.add_indicator("engulfing")
    assert "engulfing" in pc._patterns
    assert pc._patterns["engulfing"]["scatter"] is not None


def test_pattern_pick_again_toggles_off(app):
    pc, _ = _chart_with_host(app)
    pc.add_indicator("hammer")
    assert "hammer" in pc._patterns
    pc.add_indicator("hammer")          # re-pick = toggle off
    assert "hammer" not in pc._patterns


# --- ADD: pairs (vs a 2nd-symbol benchmark) ------------------------------------------------
def test_pairs_pick_emits_request_not_a_pane(app):
    pc, _ = _chart_with_host(app)
    seen = []
    pc.pairsRequested.connect(seen.append)
    pc.add_indicator("ratio")           # the app supplies the benchmark, not the chart
    assert seen == ["ratio"]
    assert "ratio" not in pc._osc_panes


def test_add_pairs_with_benchmark_creates_pane(app):
    pc, split = _chart_with_host(app)
    bench = [b.close * 0.9 for b in pc._bars]
    pc.add_pairs("spread_zscore", bench)
    assert "spread_zscore" in pc._osc_panes
    assert split.count() == 2


# --- DELETE-ALL on new data ----------------------------------------------------------------
def test_set_data_clears_oscillators_and_patterns(app):
    pc, split = _chart_with_host(app)
    pc.add_indicator("rsi")
    pc.add_indicator("engulfing")
    pc.set_data(_bars(40), [])          # new symbol/interval resets indicators
    assert not pc._osc_panes
    assert not pc._patterns
    assert split.count() == 1


# --- the category picker -------------------------------------------------------------------
def test_picker_lists_full_registry(app):
    from vike_trader_app.core.indicators import base as _base

    dlg = _IndicatorPicker()
    assert len(dlg._rows) == len(_base.list_indicators()) == 176


def test_picker_category_tab_filters(app):
    dlg = _IndicatorPicker()
    momentum_idx = next(i for i, (label, _c) in enumerate(_PICKER_TABS) if label == "Momentum")
    dlg._on_tab(momentum_idx)
    for item, _hay, cat in dlg._rows:
        assert item.isHidden() == (cat != "momentum")


def test_picker_search_filters(app):
    dlg = _IndicatorPicker()
    dlg._search.setText("relative strength")
    dlg._apply()
    visible = [item for item, _h, _c in dlg._rows if not item.isHidden()]
    names = {it.data(QtCore.Qt.UserRole) for it in visible}
    assert "rsi" in names
    assert all("relative" in h for _it, h, _c in dlg._rows
               if not _it.isHidden())


def test_picker_choose_emits_name(app):
    dlg = _IndicatorPicker()
    chosen = []
    dlg.chosen.connect(chosen.append)
    item = next(it for it, _h, _c in dlg._rows if it.data(QtCore.Qt.UserRole) == "macd")
    dlg._activate(item)
    assert chosen == ["macd"]
