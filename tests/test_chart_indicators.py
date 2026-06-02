"""Offscreen tests for the TradingView-style indicator management on the price chart:
adding, editing parameters (recompute), styling, hiding/showing, moving between panes, and
deleting — across price overlays, oscillator panes, candlestick patterns, and pairs — plus the
category picker, the settings dialog, and the per-pane legend UI.
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
    _IndicatorSettings,
    _LegendRow,
    _PaneLegend,
    OscillatorPane,
    PriceChart,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=80):
    out = []
    for i in range(n):
        c = 100 + (i % 9) - 4 + i * 0.15  # zig-zag with a slow uptrend
        out.append(Bar(ts=i * 60_000, open=c - 0.5, high=c + 1.2, low=c - 1.1, close=c))
    return out


def _chart(app):
    """A PriceChart wired to a vertical splitter pane-host (as app.py mounts it)."""
    pc = PriceChart()
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.addWidget(pc)
    pc.set_pane_host(split)
    pc.set_data(_bars(), [])
    return pc, split


def _valid(series_dict):
    return sum(1 for v in next(iter(series_dict.values())) if v is not None)


# --- ADD: routing by kind --------------------------------------------------------------------
def test_add_overlay_handle(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("ema")
    assert ind.kind == "overlay" and ind.uid in pc._indicators
    assert ind.curves and not ind.pane and not ind.scatter
    assert split.count() == 1  # overlays stay on the price pane


def test_add_oscillator_makes_pane(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("rsi")
    assert ind.kind == "oscillator"
    assert isinstance(ind.pane, OscillatorPane)
    assert split.count() == 2


def test_add_pattern_handle(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("engulfing")
    assert ind.kind == "pattern" and ind.scatter is not None


def test_add_pairs_pick_emits_request(app):
    pc, _ = _chart(app)
    seen = []
    pc.pairsRequested.connect(seen.append)
    assert pc.add_indicator("ratio") is None  # needs a benchmark -> request, no handle
    assert seen == ["ratio"]


def test_add_pairs_with_benchmark(app):
    pc, split = _chart(app)
    bench = [b.close * 0.9 for b in pc._bars]
    ind = pc.add_pairs("spread_zscore", bench)
    assert ind.kind == "pairs" and isinstance(ind.pane, OscillatorPane)
    assert split.count() == 2


def test_duplicate_indicators_allowed(app):
    pc, _ = _chart(app)
    a = pc.add_indicator("ema")
    b = pc.add_indicator("ema")
    assert a.uid != b.uid and len(pc._indicators) == 2  # TradingView allows duplicates


# --- EDIT: parameters + recompute ------------------------------------------------------------
def test_edit_params_recomputes(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    p0 = ind.spec.params[0]
    before = _valid(ind.series)
    new = dict(ind.params)
    new[p0.name] = max(int(p0.min or 2), 4)  # shorter period -> fewer warm-up gaps
    pc._apply_edit(ind.uid, new, ind.colors)
    assert pc._indicators[ind.uid].params[p0.name] == new[p0.name]
    assert _valid(pc._indicators[ind.uid].series) != before  # series actually recomputed


def test_edit_colors_applied(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")
    pc._apply_edit(ind.uid, ind.params, ["#ff0000"])
    assert pc._indicators[ind.uid].colors[0] == "#ff0000"


def test_label_includes_params(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert "RSI" in ind.label
    assert any(str(v) in ind.label for v in ind.params.values())  # e.g. "RSI 14"


# --- HIDE / SHOW -----------------------------------------------------------------------------
def test_hide_show_overlay(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")
    pc.set_indicator_visible(ind.uid, False)
    assert ind.visible is False
    assert all(not c.isVisible() for c in ind.curves.values())
    pc.set_indicator_visible(ind.uid, True)
    assert all(c.isVisible() for c in ind.curves.values())


def test_toggle_visible_helper(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pc._toggle_visible(ind.uid)
    assert ind.visible is False


# --- MOVE between panes ----------------------------------------------------------------------
def test_move_overlay_to_new_pane(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("ema")
    assert split.count() == 1
    pc.move_indicator(ind.uid, "new")
    assert pc._indicators[ind.uid].kind == "oscillator"
    assert pc._indicators[ind.uid].pane is not None and split.count() == 2


def test_move_oscillator_to_price(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("rsi")
    assert split.count() == 2
    pc.move_indicator(ind.uid, "price")
    assert pc._indicators[ind.uid].kind == "overlay"
    assert pc._indicators[ind.uid].pane is None and split.count() == 1


# --- DELETE ----------------------------------------------------------------------------------
def test_remove_overlay(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")
    pc.remove_indicator(ind.uid)
    assert ind.uid not in pc._indicators


def test_remove_oscillator_frees_pane(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("rsi")
    pc.remove_indicator(ind.uid)
    assert ind.uid not in pc._indicators and split.count() == 1


def test_remove_via_pane_signal(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("macd")
    ind.pane.removeRequested.emit(ind.uid)  # the pane's ⋯ -> Remove
    assert ind.uid not in pc._indicators and split.count() == 1


def test_set_data_clears_indicators(app):
    pc, split = _chart(app)
    pc.add_indicator("ema")
    pc.add_indicator("rsi")
    pc.add_indicator("engulfing")
    pc.set_data(_bars(40), [])
    assert pc._indicators == {} and split.count() == 1


# --- REVEAL (progressive) --------------------------------------------------------------------
def test_oscillator_reveals_in_lockstep(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane

    def _pts():
        return sum(0 if (xs := c.getData()[0]) is None else len(xs) for c in pane._curves.values())

    pane.reveal(5)
    early = _pts()
    pane.reveal(75)
    assert _pts() > early


# --- SETTINGS dialog (UI) --------------------------------------------------------------------
def test_settings_builds_input_widgets(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    assert set(dlg._param_widgets) == {p.name for p in ind.spec.params}
    assert len(dlg._color_btns) == len(ind.spec.outputs)


def test_settings_emits_params_on_ok(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    p0 = ind.spec.params[0]
    dlg._param_widgets[p0.name].setValue(9)
    got = {}
    dlg.applied.connect(lambda params, colors: got.update(params=params, colors=colors))
    dlg._accept()
    assert got["params"][p0.name] == 9
    assert len(got["colors"]) == len(ind.spec.outputs)


# --- LEGEND (UI) -----------------------------------------------------------------------------
def test_price_legend_lists_overlays_and_patterns(app):
    pc, _ = _chart(app)
    pc.add_indicator("ema")        # overlay -> on price legend
    pc.add_indicator("engulfing")  # pattern -> on price legend
    pc.add_indicator("rsi")        # oscillator -> NOT on the price legend
    assert len(pc._price_legend._rows) == 2


def test_legend_row_signals(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")
    row = _LegendRow(ind)
    fired = []
    row.editRequested.connect(lambda u: fired.append(("edit", u)))
    row.removeRequested.connect(lambda u: fired.append(("remove", u)))
    row.hideToggled.connect(lambda u: fired.append(("hide", u)))
    row.moveRequested.connect(lambda u, t: fired.append(("move", u, t)))
    row.mouseDoubleClickEvent(None)          # double-click -> edit
    row._eye.click()                          # eye -> hide toggle
    assert ("edit", ind.uid) in fired and ("hide", ind.uid) in fired


def test_oscillator_pane_has_legend_row(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert ind.pane.uid == ind.uid
    assert isinstance(ind.pane._legend, _LegendRow)


# --- the category picker (unchanged behaviour) -----------------------------------------------
def test_picker_lists_full_registry(app):
    from vike_trader_app.core.indicators import base as _base

    dlg = _IndicatorPicker()
    assert len(dlg._rows) == len(_base.list_indicators()) == 176


def test_picker_category_filter(app):
    dlg = _IndicatorPicker()
    idx = next(i for i, (label, _c) in enumerate(_PICKER_TABS) if label == "Momentum")
    dlg._on_tab(idx)
    for item, _hay, cat in dlg._rows:
        assert item.isHidden() == (cat != "momentum")


def test_picker_choose_emits_name(app):
    dlg = _IndicatorPicker()
    chosen = []
    dlg.chosen.connect(chosen.append)
    item = next(it for it, _h, _c in dlg._rows if it.data(QtCore.Qt.UserRole) == "macd")
    dlg._activate(item)
    assert chosen == ["macd"]
