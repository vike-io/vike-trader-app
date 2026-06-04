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
    _DragGrip,
    _IndicatorPicker,
    _IndicatorSettings,
    _LegendRow,
    _ObjectTree,
    _PaneLegend,
    OscillatorPane,
    PriceChart,
    TimeAxis,
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


def test_set_data_persists_indicators(app):
    pc, split = _chart(app)
    pc.add_indicator("ema")
    pc.add_indicator("rsi")
    pc.set_data(_bars(50), [])  # new symbol/interval -> indicators recomputed + kept (TV-style)
    assert len(pc._indicators) == 2 and split.count() == 2


def test_set_data_drops_pairs(app):
    pc, _ = _chart(app)
    pc.add_pairs("spread_zscore", [b.close * 0.9 for b in pc._bars])
    pc.set_data(_bars(50), [])  # benchmark was aligned to the old bars -> pairs drop
    assert not any(i.kind == "pairs" for i in pc._indicators.values())


# --- REVEAL (progressive) --------------------------------------------------------------------
def test_oscillator_reveals_in_lockstep(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane

    def _pts():
        total = 0
        for cs in pane._curves.values():  # {uid: {label: curve}}
            for c in cs.values():
                xs = c.getData()[0]
                total += 0 if xs is None else len(xs)
        return total

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
    assert ind.uid in ind.pane.uids
    assert isinstance(ind.pane._rows[ind.uid], _LegendRow)


# --- DEFERRED ACTIONS: clone / visual order / reorder / merge ------------------------------
def test_clone_duplicates_indicator(app):
    pc, _ = _chart(app)
    a = pc.add_indicator("ema")
    pc._apply_edit(a.uid, a.params, ["#abcdef"])  # give it a custom colour
    clone = pc.clone_indicator(a.uid)
    assert clone is not None and clone.uid != a.uid
    assert clone.name == "ema" and clone.colors[0] == "#abcdef"  # style copied
    assert len([i for i in pc._indicators.values() if i.name == "ema"]) == 2


def test_visual_order_changes_z(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")
    pc._indicator_action(ind.uid, "front")
    z_front = next(iter(ind.curves.values())).zValue()
    pc._indicator_action(ind.uid, "back")
    z_back = next(iter(ind.curves.values())).zValue()
    assert z_front > z_back >= 0.5  # back stays above the candles (z=0)


def test_reorder_pane(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # pane at index 1
    b = pc.add_indicator("macd")  # pane at index 2
    assert split.indexOf(b.pane) == 2
    pc.move_indicator(b.uid, "up")
    assert split.indexOf(b.pane) == 1 and split.indexOf(a.pane) == 2


def test_merge_into_existing_pane(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    assert split.count() == 3  # price + 2 oscillator panes
    pc.move_indicator(b.uid, "merge_above")  # macd merges into rsi's pane
    assert split.count() == 2                # one oscillator pane dropped
    assert a.pane is b.pane and b.pane.count() == 2
    assert set(b.pane.uids) == {a.uid, b.uid}


def test_remove_from_shared_pane_keeps_others(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc.move_indicator(b.uid, "merge_above")
    pane = a.pane
    pc.remove_indicator(b.uid)               # remove one of two -> pane survives
    assert b.uid not in pc._indicators and a.uid in pc._indicators
    assert pane.count() == 1 and split.count() == 2


def test_object_tree_lists_and_groups(app):
    pc, _ = _chart(app)
    pc.add_indicator("ema")        # price group
    pc.add_indicator("engulfing")  # price group
    pc.add_indicator("rsi")        # pane group
    tree = _ObjectTree(pc)
    rows = [tree._body.itemAt(i).widget() for i in range(tree._body.count())]
    legend_rows = [w for w in rows if isinstance(w, _LegendRow)]
    assert len(legend_rows) == 3  # one row per active indicator
    # removing through the tree drops it from the chart and the tree rebuilds
    legend_rows[0].removeRequested.emit(legend_rows[0]._uid)
    assert len(pc._indicators) == 2


# --- pin-to-scale ---------------------------------------------------------------------------
def test_pin_overlay_to_own_scale(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")
    pc._indicator_action(ind.uid, "pin_own")
    assert ind.own_scale is True and pc._vb2 is not None
    assert all(c in pc._vb2.addedItems for c in ind.curves.values())  # curves moved off price vb
    pc._indicator_action(ind.uid, "pin_price")
    assert ind.own_scale is False
    assert all(c not in pc._vb2.addedItems for c in ind.curves.values())


# --- visibility on intervals ----------------------------------------------------------------
def test_visibility_on_intervals(app):
    pc, _ = _chart(app)
    pc.set_timeframe("1m")
    ind = pc.add_indicator("ema")
    assert ind.intervals is None and ind.shown is True  # all timeframes by default
    pc._toggle_interval_visibility(ind, "1m")            # hide on the current (1m) timeframe
    assert ind.intervals is not None and "1m" not in ind.intervals
    assert ind.shown is False
    pc.set_timeframe("5m")                               # other timeframe -> shows again
    assert ind.shown is True


# --- drag-to-reorder ------------------------------------------------------------------------
def test_pane_has_drag_grip(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert isinstance(ind.pane._grip, _DragGrip)


def test_drag_reorder_moves_pane(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # pane index 1
    b = pc.add_indicator("macd")  # pane index 2
    pc._drag_pane(b.pane, -100000)  # cursor far above any neighbour centre -> move up
    assert split.indexOf(b.pane) == 1 and split.indexOf(a.pane) == 2


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


# --- PHASE 1: time alignment ----------------------------------------------------------------
def test_panes_in_visual_order_matches_splitter(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # pane at splitter index 1
    b = pc.add_indicator("macd")  # pane at splitter index 2
    assert pc._panes_in_visual_order() == [a.pane, b.pane]


def test_panes_in_visual_order_differs_from_osc_after_drag(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # pane index 1
    b = pc.add_indicator("macd")  # pane index 2
    pc._drag_pane(b.pane, -100000)  # cursor far above -> b moves to index 1
    assert split.indexOf(b.pane) == 1 and split.indexOf(a.pane) == 2
    # _osc_panes() is dict-insertion order (a, b); visual order now follows the splitter (b, a)
    assert pc._osc_panes() == [a.pane, b.pane]
    assert pc._panes_in_visual_order() == [b.pane, a.pane]
    assert pc._panes_in_visual_order() != pc._osc_panes()


def test_axis_natural_width_exceeds_pyqtgraph_stale_width(app):
    # The price axis shows wide labels (e.g. "117.50"); its natural width must reflect that,
    # NOT the stale/default AxisItem.width() (~35 before a paint pass).
    pc, _ = _chart(app)
    ax = pc.getAxis("right")
    nat = pc._axis_natural_width(ax)
    assert nat > 50  # padded up from the longest current tick string, not the stale default


def test_axis_natural_width_price_wider_than_oscillator(app):
    # Price labels ("117.50") are wider than a 0-100 RSI pane's ("70.00"): proves the
    # measurement keys off each axis's OWN current tick strings.
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    price_nat = pc._axis_natural_width(pc.getAxis("right"))
    osc_nat = pc._axis_natural_width(ind.pane.getAxis("right"))
    assert price_nat > osc_nat


def test_sync_axis_width_equalizes_above_narrow_pane(app):
    # After equalize, every right axis shares ONE width == the widest natural width,
    # which is strictly GREATER than the narrow oscillator's own natural width
    # (proves padding-up happened, not "two zeros are equal").
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    osc_ax = ind.pane.getAxis("right")
    osc_nat = pc._axis_natural_width(osc_ax)
    pc._sync_axis_width()
    price_w = pc.getAxis("right").width()
    osc_w = osc_ax.width()
    assert price_w == osc_w            # equalized
    assert osc_w > osc_nat             # the narrow pane was padded up to the price width


def test_sync_axis_width_no_recursion(app):
    # The _wsyncing guard must break the setWidth -> resize -> sigResized -> re-sync loop:
    # a re-entrant call while syncing is a no-op.
    pc, _ = _chart(app)
    pc.add_indicator("rsi")
    calls = []
    real_natural = pc._axis_natural_width
    pc._axis_natural_width = lambda ax: (calls.append(1), real_natural(ax))[1]
    pc._wsyncing = True          # simulate "already inside a sync"
    pc._sync_axis_width()        # must early-return, measuring nothing
    assert calls == []
    pc._wsyncing = False
    pc._sync_axis_width()        # now it runs and measures
    assert calls


def test_oscillator_pane_has_hidden_bottom_time_axis(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    assert isinstance(pane._time_axis, TimeAxis)
    assert pane.getAxis("bottom") is pane._time_axis
    # After Task 7 wiring: _new_pane calls _align_panes, so the single pane becomes the
    # lowest pane and its bottom axis is shown immediately (price chart hides its own).
    assert pane.getAxis("bottom").isVisible() is True


def test_oscillator_pane_set_bars_feeds_time_axis(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    bs = _bars(30)
    pane.set_bars(bs)
    assert pane._time_axis._bars is bs


def test_oscillator_pane_set_bottom_axis_visible_toggles(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.set_bottom_axis_visible(True)
    assert pane.getAxis("bottom").isVisible() is True
    pane.set_bottom_axis_visible(False)
    assert pane.getAxis("bottom").isVisible() is False


def test_reassign_bottom_axis_zero_panes_keeps_price_axis(app):
    pc, _ = _chart(app)
    pc._reassign_bottom_axis()
    assert pc.getAxis("bottom").isVisible() is True
    assert pc._time_axis._bars is pc._bars


def test_reassign_bottom_axis_moves_to_lowest_pane(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # index 1
    b = pc.add_indicator("macd")  # index 2 (lowest)
    pc._reassign_bottom_axis()
    # exactly one visible bottom axis: the lowest pane's
    assert pc.getAxis("bottom").isVisible() is False
    assert a.pane.getAxis("bottom").isVisible() is False
    assert b.pane.getAxis("bottom").isVisible() is True
    # every pane axis was fed the same bars as the price chart
    assert a.pane._time_axis._bars is pc._bars
    assert b.pane._time_axis._bars is pc._bars


def test_reassign_bottom_axis_follows_reorder(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._reassign_bottom_axis()
    assert b.pane.getAxis("bottom").isVisible() is True
    pc._drag_pane(b.pane, -100000)  # b -> index 1, a -> index 2 (now lowest)
    pc._reassign_bottom_axis()
    assert a.pane.getAxis("bottom").isVisible() is True
    assert b.pane.getAxis("bottom").isVisible() is False


def test_reassign_bottom_axis_syncs_vb2(app):
    # Hiding the price bottom axis grows the price ViewBox; _vb2 must re-sync so own-scale
    # overlays don't misalign. _reassign_bottom_axis calls _sync_vb2() explicitly.
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ema = pc.add_indicator("ema")
    pc._indicator_action(ema.uid, "pin_own")  # creates _vb2
    pc.add_indicator("rsi")                    # adds a pane -> price bottom axis will hide
    pc._reassign_bottom_axis()
    pc.getPlotItem().layout.activate()
    app.processEvents()
    vb = pc.getViewBox().sceneBoundingRect()
    vb2 = pc._vb2.sceneBoundingRect()
    assert abs(vb.height() - vb2.height()) < 2.0  # _vb2 tracks the (grown) price viewbox
    assert abs(vb.top() - vb2.top()) < 2.0


def test_align_panes_zero_panes_is_safe(app):
    pc, _ = _chart(app)
    pc._align_panes()  # must not raise with no panes
    assert pc.getAxis("bottom").isVisible() is True


def test_align_panes_reassigns_then_equalizes(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._align_panes()
    # bottom axis is reassigned to the lowest pane...
    assert b.pane.getAxis("bottom").isVisible() is True
    assert pc.getAxis("bottom").isVisible() is False
    # ...AND all right axes are equalized to one shared width
    w = pc.getAxis("right").width()
    assert a.pane.getAxis("right").width() == w
    assert b.pane.getAxis("right").width() == w


def test_align_panes_order_reassign_before_sync(app):
    # _reassign_bottom_axis (which changes axis visibility/natural width) must run BEFORE
    # _sync_axis_width, so the equalize sees the final visible axes.
    pc, _ = _chart(app)
    pc.add_indicator("rsi")
    order = []
    pc._reassign_bottom_axis = lambda: order.append("reassign")
    pc._sync_axis_width = lambda: order.append("sync")
    pc._align_panes()
    assert order == ["reassign", "sync"]


def test_set_data_aligns_panes(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc.set_data(_bars(50), [])  # re-feeds pane axes + re-equalizes
    # pane axes were re-fed the new bars
    assert len(a.pane._time_axis._bars) == 50
    assert len(b.pane._time_axis._bars) == 50
    # bottom axis still on the lowest pane, widths still equal
    assert b.pane.getAxis("bottom").isVisible() is True
    w = pc.getAxis("right").width()
    assert a.pane.getAxis("right").width() == w and b.pane.getAxis("right").width() == w


def test_apply_live_feeds_pane_axes(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pc.apply_live(_bars(90))
    assert len(ind.pane._time_axis._bars) == 90


def test_new_pane_seeds_bars_and_equalizes_on_add(app):
    # A fresh pane's time axis isn't blank, and widths equalize on the very first add
    # (the stale-width bug: equalize must work without a paint pass).
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert ind.pane._time_axis._bars  # seeded, not blank
    assert pc.getAxis("right").width() == ind.pane.getAxis("right").width()


def test_remove_last_pane_restores_price_axis(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("rsi")
    assert pc.getAxis("bottom").isVisible() is False
    pc.remove_indicator(ind.uid)  # _unrender drops the pane + _align_panes
    assert split.count() == 1
    assert pc.getAxis("bottom").isVisible() is True
    # right axis restored to auto (fixedWidth cleared) so a lone chart isn't pinned
    assert pc.getAxis("right").fixedWidth is None


def test_reorder_keeps_bottom_axis_on_lowest(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc.move_indicator(b.uid, "up")  # b -> index 1, a -> index 2 (lowest)
    assert a.pane.getAxis("bottom").isVisible() is True
    assert b.pane.getAxis("bottom").isVisible() is False
    w = pc.getAxis("right").width()
    assert a.pane.getAxis("right").width() == w and b.pane.getAxis("right").width() == w


def test_drag_reorder_keeps_bottom_axis_on_lowest(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._drag_pane(b.pane, -100000)  # b -> index 1, a -> index 2 (lowest)
    assert a.pane.getAxis("bottom").isVisible() is True
    assert b.pane.getAxis("bottom").isVisible() is False


def test_merge_keeps_bottom_axis_and_widths(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc.move_indicator(b.uid, "merge_above")  # macd merges into rsi's pane; one pane drops
    assert split.count() == 2
    assert a.pane.getAxis("bottom").isVisible() is True
    assert pc.getAxis("right").width() == a.pane.getAxis("right").width()


def test_move_to_new_then_price_realigns(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("ema")
    pc.move_indicator(ind.uid, "new")    # overlay -> own pane
    assert ind.pane.getAxis("bottom").isVisible() is True
    pc.move_indicator(ind.uid, "price")  # back to price overlay -> no panes
    assert split.count() == 1
    assert pc.getAxis("bottom").isVisible() is True


def test_set_timeframe_realigns(app):
    pc, _ = _chart(app)
    pc.set_timeframe("1m")
    ind = pc.add_indicator("rsi")
    pc.set_timeframe("5m")  # must keep the bottom axis on the lowest pane + widths equal
    assert ind.pane.getAxis("bottom").isVisible() is True
    assert pc.getAxis("right").width() == ind.pane.getAxis("right").width()


def test_oscillator_pane_has_min_height(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert ind.pane.minimumHeight() >= 64


def test_resize_panes_gives_lowest_pane_axis_strip(app):
    # The lowest pane carries the bottom time-axis strip; _resize_panes adds that strip to its
    # allotment so its PLOT area matches the panes above it.
    pc, split = _chart(app)
    split.resize(900, 700)
    a = pc.add_indicator("rsi")   # index 1
    b = pc.add_indicator("macd")  # index 2 (lowest)
    pc._align_panes()
    pc._resize_panes()
    sizes = split.sizes()
    # lowest pane (last entry) is the tallest among the oscillator panes (it owns the axis strip)
    assert sizes[-1] >= sizes[1]
    assert sizes[-1] - sizes[1] <= 40  # ~one axis strip taller, not wildly different


def test_crosshair_time_tag_hidden_when_panes_exist(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    pc.add_indicator("rsi")  # a pane now owns the time axis -> price-chart time tag is orphaned
    # simulate a hover inside the price viewbox
    vb = pc.getViewBox()
    center = vb.sceneBoundingRect().center()
    pc._on_mouse_moved(center)
    assert pc._cx_time_tag.isVisible() is False   # orphaned tag stays hidden
    assert pc._cx_v.isVisible() is True           # the vertical crosshair still works


def test_crosshair_time_tag_shown_with_no_panes(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    vb = pc.getViewBox()
    center = vb.sceneBoundingRect().center()
    pc._on_mouse_moved(center)
    assert pc._cx_time_tag.isVisible() is True  # price chart owns the bottom axis -> tag shows


def test_studio_second_chart_aligns_independently(app):
    # Two independent PriceChart instances (main + studio) on separate splitters: each must
    # align its own panes with no shared/leaked state.
    main_pc, main_split = _chart(app)
    studio_pc, studio_split = _chart(app)
    main_ind = main_pc.add_indicator("rsi")
    studio_a = studio_pc.add_indicator("rsi")
    studio_b = studio_pc.add_indicator("macd")
    # main: single pane owns the bottom axis + equal widths
    assert main_ind.pane.getAxis("bottom").isVisible() is True
    assert main_pc.getAxis("right").width() == main_ind.pane.getAxis("right").width()
    # studio: lowest of its TWO panes owns the bottom axis; its widths equalize on its own axes
    assert studio_b.pane.getAxis("bottom").isVisible() is True
    assert studio_a.pane.getAxis("bottom").isVisible() is False
    sw = studio_pc.getAxis("right").width()
    assert studio_a.pane.getAxis("right").width() == sw
    assert studio_b.pane.getAxis("right").width() == sw
    # no cross-talk: neither chart's guard leaked
    assert main_pc._wsyncing is False and studio_pc._wsyncing is False
