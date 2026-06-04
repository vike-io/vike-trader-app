"""Offscreen tests for the TradingView-style indicator management on the price chart:
adding, editing parameters (recompute), styling, hiding/showing, moving between panes, and
deleting — across price overlays, oscillator panes, candlestick patterns, and pairs — plus the
category picker, the settings dialog, and the per-pane legend UI.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

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
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source: got.update(
            params=params, colors=colors, widths=widths, styles=styles, intervals=intervals,
            source=source,
        )
    )
    dlg._accept()
    assert got["params"][p0.name] == 9
    assert len(got["colors"]) == len(ind.spec.outputs)
    assert len(got["widths"]) == len(ind.spec.outputs)
    assert len(got["styles"]) == len(ind.spec.outputs)


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


def test_oscillator_pane_bottom_axis_pane_owned(app):
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


def test_crosshair_time_tag_rehomed_to_lowest_pane(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ind = pc.add_indicator("rsi")  # a pane now owns the time axis -> tag re-homes onto it
    # simulate a hover inside the price viewbox
    vb = pc.getViewBox()
    center = vb.sceneBoundingRect().center()
    pc._on_mouse_moved(center)
    assert pc._cx_time_tag.isHidden() is True       # price-chart time tag stays hidden (re-homed)
    assert pc._cx_v.isVisible() is True             # the vertical crosshair still works
    # the lowest pane now shows the time tag, and its vertical line fanned out
    low = pc._panes_in_visual_order()[-1]
    assert low is ind.pane
    assert not low._cx_time_tag.isHidden()
    assert low._cx_v.isVisible() is True


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


# --- PHASE 2: pane hover toolbar ----------------------------------------------------------------
def test_pane_icon_renders_all_kinds(app):
    from vike_trader_app.ui.chart import _pane_icon
    for kind in ("up", "down", "max", "restore", "del"):
        ic = _pane_icon(kind)
        assert isinstance(ic, QtGui.QIcon)
        assert not ic.isNull()
        pm = ic.pixmap(18, 18)
        assert not pm.isNull() and pm.width() > 0


def test_pane_toolbar_signals_and_state(app):
    from vike_trader_app.ui.chart import _PaneToolbar
    tb = _PaneToolbar()
    fired = []
    tb.moveUp.connect(lambda: fired.append("up"))
    tb.moveDown.connect(lambda: fired.append("down"))
    tb.maximizeToggled.connect(lambda: fired.append("max"))
    tb.deletePane.connect(lambda: fired.append("del"))
    tb._up.click()
    tb._down.click()
    tb._max.click()
    tb._del.click()
    assert fired == ["up", "down", "max", "del"]
    tb.set_can_up(False)
    assert not tb._up.isEnabled()
    tb.set_can_down(False)
    assert not tb._down.isEnabled()
    tb.set_can_up(True)
    tb.set_can_down(True)
    assert tb._up.isEnabled() and tb._down.isEnabled()
    tb.set_maximized(True)  # swaps glyph/tooltip, must not crash
    tb.set_maximized(False)
    assert len(tb.findChildren(QtWidgets.QToolButton)) == 4


def test_oscillator_pane_has_toolbar_and_signals(app):
    from vike_trader_app.ui.chart import _PaneToolbar
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    assert isinstance(pane._toolbar, _PaneToolbar)
    # the 4 pane-level Signal(object) carry the pane itself
    seen = []
    pane.paneMoveUp.connect(seen.append)
    pane.paneMoveDown.connect(seen.append)
    pane.paneMaximizeToggled.connect(seen.append)
    pane.paneDeleteRequested.connect(seen.append)
    pane.paneMoveUp.emit(pane)
    pane.paneMaximizeToggled.emit(pane)
    assert seen == [pane, pane]
    # toolbar tucks left of the right axis: x = width - axis_w - toolbar_w - 4
    pane.resize(400, 120)
    pane._position_toolbar()
    axis_w = int(pane.getAxis("right").width())
    expected = max(0, pane.width() - axis_w - pane._toolbar.width() - 4)
    assert pane._toolbar.x() == expected and pane._toolbar.y() == 3


def test_oscillator_pane_hover_shows_hides_toolbar(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 120)
    pane._toolbar.hide()
    pane.enterEvent(None)
    # In offscreen mode the pane has no visible parent, so isVisible() is always False;
    # use not isHidden() which tracks the explicit show()/hide() state regardless of hierarchy.
    assert not pane._toolbar.isHidden()
    pane.leaveEvent(None)
    assert pane._toolbar.isHidden()


def test_new_pane_wires_toolbar_and_refreshes(app):
    pc, _ = _chart(app)
    assert pc._maximized_pane is None and pc._saved_sizes is None
    a = pc.add_indicator("rsi")   # pane index 1 (top & bottom: only pane)
    # single pane: can move neither up nor down
    assert not a.pane._toolbar._up.isEnabled()
    assert not a.pane._toolbar._down.isEnabled()
    b = pc.add_indicator("macd")  # pane index 2
    panes = pc._panes_in_visual_order()
    top, bottom = panes[0], panes[-1]
    # top pane: up disabled, down enabled; bottom pane: up enabled, down disabled
    assert not top._toolbar._up.isEnabled() and top._toolbar._down.isEnabled()
    assert bottom._toolbar._up.isEnabled() and not bottom._toolbar._down.isEnabled()


def test_pane_move_up_down_reorders_splitter(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # pane index 1
    b = pc.add_indicator("macd")  # pane index 2
    assert split.indexOf(b.pane) == 2
    pc._pane_move_up(b.pane)
    assert split.indexOf(b.pane) == 1 and split.indexOf(a.pane) == 2
    pc._pane_move_down(b.pane)
    assert split.indexOf(b.pane) == 2 and split.indexOf(a.pane) == 1


def test_pane_move_clamps_at_edges(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # index 1
    b = pc.add_indicator("macd")  # index 2
    pc._pane_move_up(a.pane)      # already topmost (index 1) -> no-op (never above price@0)
    assert split.indexOf(a.pane) == 1
    pc._pane_move_down(b.pane)    # already bottom -> no-op
    assert split.indexOf(b.pane) == 2


def test_after_pane_reorder_realigns(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._pane_move_up(b.pane)
    # bottom axis follows to the new lowest pane (Phase 1 _align_panes)
    bottom = pc._panes_in_visual_order()[-1]
    assert bottom.getAxis("bottom").isVisible()
    assert not pc._panes_in_visual_order()[0].getAxis("bottom").isVisible()
    # toolbars updated: new top can't go up, new bottom can't go down
    panes = pc._panes_in_visual_order()
    assert not panes[0]._toolbar._up.isEnabled()
    assert not panes[-1]._toolbar._down.isEnabled()


def test_delete_pane_drops_single_indicator(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    assert split.count() == 2
    pc._delete_pane(a.pane)
    assert a.uid not in pc._indicators and split.count() == 1


def test_delete_merged_pane_removes_all_indicators(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc.move_indicator(b.uid, "merge_above")   # both now share one pane
    assert split.count() == 2 and a.pane is b.pane
    pane = a.pane
    pc._delete_pane(pane)
    assert a.uid not in pc._indicators and b.uid not in pc._indicators
    assert split.count() == 1


def test_resize_panes_noop_while_maximized(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    pc.add_indicator("macd")
    split.resize(400, 600)
    pc._maximized_pane = a.pane          # simulate a maximized pane
    sentinel = [10, 700, 50]             # deliberately uneven, not what _resize_panes would set
    split.setSizes(sentinel)
    before = split.sizes()
    pc._resize_panes()                   # must early-return (no stomping)
    assert split.sizes() == before


def test_unrender_pane_drop_clears_maximize_lock(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    pc._maximized_pane = a.pane
    pc.remove_indicator(a.uid)           # drops the pane via _unrender
    assert pc._maximized_pane is None and split.count() == 1


def test_maximize_gives_dominant_share_with_price_floor(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    split.resize(400, 1000)
    pc._toggle_maximize_pane(a.pane)
    assert pc._maximized_pane is a.pane
    assert a.pane._toolbar._max.toolTip() == "Restore pane"
    sizes = split.sizes()
    total = sum(sizes)
    # price keeps a real floor (max(140, total*0.15)), the maximized pane gets the dominant share
    price_floor = max(140, int(total * 0.15))
    assert sizes[0] >= price_floor - 1          # OHLC stays visible (TV)
    a_idx = split.indexOf(a.pane)
    assert sizes[a_idx] == max(sizes[1:])       # maximized pane is the biggest pane
    assert sizes[a_idx] > sizes[split.indexOf(b.pane)]


def test_restore_preserves_user_dragged_sizes(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    pc.add_indicator("macd")
    split.resize(400, 1000)
    user = [600, 250, 150]
    split.setSizes(user)
    snap = split.sizes()                         # what Qt actually stored
    pc._toggle_maximize_pane(a.pane)             # saves snap
    pc._toggle_maximize_pane(a.pane)             # restore: same count -> replay saved sizes
    assert pc._maximized_pane is None
    assert split.sizes() == snap
    assert a.pane._toolbar._max.toolTip() == "Maximize pane"


def test_delete_maximized_pane_clears_lock(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    pc.add_indicator("macd")
    pc._toggle_maximize_pane(a.pane)
    assert pc._maximized_pane is a.pane
    pc._delete_pane(a.pane)
    assert pc._maximized_pane is None            # no dangling deleted-QWidget ref
    assert a.uid not in pc._indicators and split.count() == 2


def test_splitter_drag_clears_maximize_lock(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    pc.add_indicator("macd")
    pc._toggle_maximize_pane(a.pane)
    assert pc._maximized_pane is a.pane
    split.splitterMoved.emit(0, 1)              # a manual drag of a handle
    assert pc._maximized_pane is None            # exits maximize, like TV
    assert a.pane._toolbar._max.toolTip() == "Maximize pane"


def test_toolbar_clears_right_axis_after_layout(app):
    pc, split = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    split.resize(500, 600)
    pane.resize(500, 140)
    pc.show_upto(len(pc._bars) - 1)   # data settles -> axis width known
    pc._refresh_pane_toolbars()
    tb = pane._toolbar
    axis_w = int(pane.getAxis("right").width())
    # the toolbar's right edge must sit left of the price-axis labels (cleared, like TV)
    assert tb.x() + tb.width() <= pane.width() - axis_w + 1
    assert tb.x() >= 0


def test_studio_instance_pane_toolbar_parity(app):
    # two independent PriceChart instances must not share maximize/toolbar state
    pc_a, split_a = _chart(app)
    pc_b, split_b = _chart(app)
    a = pc_a.add_indicator("rsi")
    b = pc_b.add_indicator("macd")
    pc_a._toggle_maximize_pane(a.pane)
    assert pc_a._maximized_pane is a.pane
    assert pc_b._maximized_pane is None          # state is per-instance, not class-shared
    # each pane has its own toolbar wired to its own chart
    assert isinstance(a.pane._toolbar, type(b.pane._toolbar))
    b.pane.paneDeleteRequested.emit(b.pane)      # routed to pc_b._delete_pane
    assert b.uid not in pc_b._indicators and split_b.count() == 1
    assert a.uid in pc_a._indicators             # unaffected


# --- Fix A: grip drag-reorder must refresh toolbar enabled-states ----------------------------
def test_drag_reorder_refreshes_toolbar_state(app):
    """_drag_pane must call _refresh_pane_toolbars so enabled-states reflect the new order."""
    pc, split = _chart(app)
    pc.add_indicator("rsi")       # pane index 1 (visual 0)
    b = pc.add_indicator("macd")  # pane index 2 (visual 1)
    # drag bottom pane (b) far above -> b becomes visual index 0, a becomes visual index 1
    pc._drag_pane(b.pane, -100000)
    panes = pc._panes_in_visual_order()
    top_pane = panes[0]   # b.pane is now at the top
    bottom_pane = panes[-1]  # a.pane is now at the bottom
    # top pane: move-up must be disabled (nothing above it)
    assert not top_pane._toolbar._up.isEnabled(), "top pane move-up should be disabled after drag"
    # bottom pane: move-down must be disabled (nothing below it)
    assert not bottom_pane._toolbar._down.isEnabled(), "bottom pane move-down should be disabled after drag"


# --- Fix B: grip drag-reorder while maximized must exit maximize ----------------------------
def test_drag_reorder_clears_maximize_lock(app):
    """_drag_pane must clear the maximize lock (like _on_splitter_moved) when it reorders."""
    pc, split = _chart(app)
    pc.add_indicator("rsi")       # pane index 1
    b = pc.add_indicator("macd")  # pane index 2
    # maximize one pane
    pc._toggle_maximize_pane(b.pane)
    assert pc._maximized_pane is b.pane
    # perform a grip drag-reorder (b moves up, swapping with a)
    pc._drag_pane(b.pane, -100000)
    # maximize lock must have been cleared
    assert pc._maximized_pane is None, "_drag_pane must clear _maximized_pane (exit maximize)"
    assert not b.pane._toolbar._max.toolTip() == "Restore pane", \
        "dragged pane toolbar should no longer show Restore glyph"


# --- Minor #5: 120 ms re-check branch keeps toolbar visible when cursor is still inside ------
def test_toolbar_maybe_hide_rechecks_cursor(app):
    """_maybe_hide_toolbar hides only when cursor is outside; stays visible when inside."""
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 120)
    pane._toolbar.show()
    # monkeypatch: cursor is inside -> _maybe_hide_toolbar must NOT hide
    pane._cursor_in_rect = lambda: True
    pane._maybe_hide_toolbar()
    assert not pane._toolbar.isHidden(), "toolbar must stay visible when cursor is inside"
    # now cursor leaves -> _maybe_hide_toolbar must hide
    pane._cursor_in_rect = lambda: False
    pane._maybe_hide_toolbar()
    assert pane._toolbar.isHidden(), "toolbar must hide when cursor is outside"


# --- PHASE 3: settings dialog parity --------------------------------------------------------
def test_fresh_add_has_default_widths_and_styles(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("macd")  # 3 outputs
    n = len(ind.spec.outputs)
    assert ind.widths == [1] * n
    assert ind.styles == ["solid"] * n


def test_indicator_spec_defaults_single_source(app):
    from vike_trader_app.ui.chart import _Indicator
    from vike_trader_app.core.indicators import base as _base
    spec = _base.get("macd")
    params, colors, widths, styles, source = _Indicator.spec_defaults(spec)
    assert params == {p.name: p.default for p in spec.params}
    assert len(colors) == len(spec.outputs)
    assert widths == [1] * len(spec.outputs)
    assert styles == ["solid"] * len(spec.outputs)
    assert source == "close"


def test_pen_style_maps_names_to_qt(app):
    from vike_trader_app.ui.chart import _pen_style, _LINE_STYLES, _LINE_WIDTHS, _UNSET
    assert _pen_style("solid") == QtCore.Qt.SolidLine
    assert _pen_style("dashed") == QtCore.Qt.DashLine
    assert _pen_style("dotted") == QtCore.Qt.DotLine
    assert _pen_style("bogus") == QtCore.Qt.SolidLine  # unknown -> solid
    assert [v for _lbl, v in _LINE_STYLES] == ["solid", "dashed", "dotted"]
    assert list(_LINE_WIDTHS) == [1, 2, 3, 4]
    assert _UNSET is not None and _UNSET != [] and _UNSET != {}  # a distinct sentinel


def test_render_pens_use_width_and_style_overlay(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")  # overlay, 1 output
    ind.widths = [3]
    ind.styles = ["dashed"]
    pc._unrender(ind)
    pc._render(ind)  # rebuilds the overlay PlotDataItem pen
    curve = next(iter(ind.curves.values()))
    pen = curve.opts["pen"]
    assert pen.width() == 3
    assert pen.style() == QtCore.Qt.DashLine


def test_build_curves_pens_use_width_and_style_oscillator(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")  # oscillator, 1 output
    ind.widths = [4]
    ind.styles = ["dotted"]
    ind.pane.update_ind(ind)  # rebuilds curves via _build_curves
    curve = next(iter(ind.pane._curves[ind.uid].values()))
    pen = curve.opts["pen"]
    assert pen.width() == 4
    assert pen.style() == QtCore.Qt.DotLine


def test_all_intervals_and_normalize_helpers(app):
    from vike_trader_app.ui.chart import _all_intervals, _normalize_intervals, _TIMEFRAMES
    expected = [iv for _sec, items in _TIMEFRAMES for _lbl, iv in items]
    assert _all_intervals() == expected
    # every interval checked -> None (shows on all)
    assert _normalize_intervals(set(expected)) is None
    # a strict subset stays a set
    sub = set(expected[:-1])
    assert _normalize_intervals(sub) == sub


def test_settings_style_tab_combos_round_trip(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("macd")  # 3 outputs, line kind
    dlg = _IndicatorSettings(ind)
    assert len(dlg._width_combos) == len(ind.spec.outputs)
    assert len(dlg._style_combos) == len(ind.spec.outputs)
    # combos carry typed userData, not display text
    dlg._width_combos[0].setCurrentIndex(2)  # _LINE_WIDTHS[2] == 3
    si = next(i for i in range(dlg._style_combos[0].count())
              if dlg._style_combos[0].itemData(i) == "dashed")
    dlg._style_combos[0].setCurrentIndex(si)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals: got.update(
            widths=widths, styles=styles
        )
    )
    dlg._accept()
    assert got["widths"][0] == 3 and isinstance(got["widths"][0], int)
    assert got["styles"][0] == "dashed"


def test_settings_pattern_hides_width_and_style(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("engulfing")  # kind == 'pattern' -> markers, no pens
    dlg = _IndicatorSettings(ind)
    assert all(c.isHidden() for c in dlg._width_combos)
    assert all(c.isHidden() for c in dlg._style_combos)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals: got.update(widths=widths)
    )
    dlg._accept()  # must not crash for a pattern indicator
    assert "widths" in got


def test_settings_visibility_tab_covers_all_intervals(app):
    from vike_trader_app.ui.chart import _all_intervals
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    assert set(dlg._iv_checks) == set(_all_intervals())
    assert all(cb.isChecked() for cb in dlg._iv_checks.values())  # intervals None -> all checked


def test_settings_visibility_uncheck_emits_subset(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    dlg._iv_checks["1m"].setChecked(False)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals: got.update(intervals=intervals)
    )
    dlg._accept()
    assert got["intervals"] is not None and "1m" not in got["intervals"]


def test_settings_visibility_all_checked_emits_none(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    for cb in dlg._iv_checks.values():
        cb.setChecked(True)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals: got.update(intervals=intervals)
    )
    dlg._accept()
    assert got["intervals"] is None  # all checked -> shows everywhere


def test_settings_visibility_seeds_from_existing_set(app):
    from vike_trader_app.ui.chart import _all_intervals
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    ind.intervals = {iv for iv in _all_intervals() if iv != "1m"}  # restricted set
    dlg = _IndicatorSettings(ind)
    assert dlg._iv_checks["1m"].isChecked() is False
    assert dlg._iv_checks["5m"].isChecked() is True


def test_settings_defaults_button_resets_form_without_emitting(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    # mutate stored state away from defaults so a reset is observable
    ind.params[ind.spec.params[0].name] = 99
    ind.widths = [4]
    ind.styles = ["dotted"]
    ind.intervals = {"5m"}
    dlg = _IndicatorSettings(ind)
    emitted = []
    dlg.applied.connect(lambda *a: emitted.append(a))
    dlg._reset_defaults()
    # form widgets snap back to spec defaults
    p0 = ind.spec.params[0]
    assert dlg._param_widgets[p0.name].value() == p0.default
    assert dlg._width_combos[0].currentData() == 1
    assert dlg._style_combos[0].currentData() == "solid"
    assert all(cb.isChecked() for cb in dlg._iv_checks.values())  # all intervals re-checked
    assert emitted == []          # Defaults does NOT emit
    assert dlg.isVisible() or not dlg.isVisible()  # and does NOT close (not rejected/accepted)
    assert ind.params[p0.name] == 99  # stored indicator untouched until Ok


def test_apply_edit_sets_width_style_intervals_overlay(app):
    pc, _ = _chart(app)
    pc.set_timeframe("1m")
    ind = pc.add_indicator("ema")  # overlay branch
    pc._apply_edit(ind.uid, dict(ind.params), list(ind.colors),
                   widths=[3], styles=["dashed"], intervals={"5m"})
    ind = pc._indicators[ind.uid]
    assert ind.widths == [3] and ind.styles == ["dashed"]
    assert ind.intervals == {"5m"}
    assert ind.shown is False  # 1m chart, restricted to 5m -> _sync_shown ran
    pen = next(iter(ind.curves.values())).opts["pen"]
    assert pen.width() == 3 and pen.style() == QtCore.Qt.DashLine


def test_apply_edit_intervals_apply_in_oscillator_branch(app):
    pc, _ = _chart(app)
    pc.set_timeframe("1m")
    ind = pc.add_indicator("rsi")  # oscillator branch (the one that never re-synced shown)
    assert ind.shown is True
    pc._apply_edit(ind.uid, dict(ind.params), list(ind.colors), intervals={"5m"})
    ind = pc._indicators[ind.uid]
    assert ind.intervals == {"5m"}
    assert ind.shown is False  # interval edit takes effect immediately, no timeframe change
    curve = next(iter(ind.pane._curves[ind.uid].values()))
    assert curve.isVisible() is False


def test_apply_edit_width_style_oscillator_pen(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pc._apply_edit(ind.uid, dict(ind.params), list(ind.colors),
                   widths=[4], styles=["dotted"])
    ind = pc._indicators[ind.uid]
    pen = next(iter(ind.pane._curves[ind.uid].values())).opts["pen"]
    assert pen.width() == 4 and pen.style() == QtCore.Qt.DotLine


def test_apply_edit_positional_callers_still_work(app):
    # the existing 3-positional-arg call path (used by tests + clone) stays valid
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    before = _valid(ind.series)
    new = dict(ind.params)
    new[ind.spec.params[0].name] = 4
    pc._apply_edit(ind.uid, new, ind.colors)  # no widths/styles/intervals -> _UNSET guards
    ind = pc._indicators[ind.uid]
    assert _valid(ind.series) != before
    assert ind.widths == [1] and ind.styles == ["solid"]  # untouched


def test_clone_copies_width_style_intervals(app):
    pc, _ = _chart(app)
    a = pc.add_indicator("rsi")
    a.widths = [3]
    a.styles = ["dashed"]
    a.intervals = {"5m"}
    clone = pc.clone_indicator(a.uid)
    assert clone is not None and clone.uid != a.uid
    clone = pc._indicators[clone.uid]
    assert clone.widths == [3]
    assert clone.styles == ["dashed"]
    assert clone.intervals == {"5m"}


def test_edit_indicator_dialog_round_trip_applies(app):
    # exercise the full edit_indicator -> dialog.applied -> _apply_edit path without exec()
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind, pc)
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source, u=ind.uid: pc._apply_edit(
            u, params, colors, widths=widths, styles=styles, intervals=intervals, source=source
        )
    )
    dlg._width_combos[0].setCurrentIndex(1)  # _LINE_WIDTHS[1] == 2
    dlg._iv_checks["1m"].setChecked(False)
    dlg._accept()
    ind = pc._indicators[ind.uid]
    assert ind.widths[0] == 2
    assert ind.intervals is not None and "1m" not in ind.intervals
    pen = next(iter(ind.pane._curves[ind.uid].values())).opts["pen"]
    assert pen.width() == 2


# --- maximize-lock lifetime: data-swap + move-to-new ----------------------------------------
def test_set_data_while_maximized_keeps_lock_and_layout(app):
    """A symbol/data swap (set_data) while a pane is maximized must NOT stomp the maximized
    layout or leave a dangling/stuck lock.  _recompute_indicators recomputes in-place without
    tearing down the pane objects, so the lock should reference the SAME live pane after the
    swap; calling toggle again must restore cleanly."""
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    pc.add_indicator("macd")
    pane_a = a.pane

    # maximize pane A
    pc._toggle_maximize_pane(pane_a)
    assert pc._maximized_pane is pane_a

    # simulate a symbol/interval change: new bars, existing indicators persist via
    # _recompute_indicators (pane objects are kept, not rebuilt)
    pc.set_data(_bars(120), [])

    # the pane object must still be alive in the splitter (not deleted)
    assert pane_a in pc._panes_in_visual_order(), (
        "BUG: set_data destroyed the maximized pane object; _maximized_pane would dangle"
    )
    # the lock must still reference that live pane (no silent clear, no dangle)
    assert pc._maximized_pane is pane_a, (
        "BUG: set_data unexpectedly cleared _maximized_pane while the pane is still live"
    )

    # splitter count unchanged: price + 2 oscillator panes
    assert split.count() == 3

    # restore: toggle again -> lock clears, sizes replay, toolbar resets
    pc._toggle_maximize_pane(pane_a)
    assert pc._maximized_pane is None
    assert split.count() == 3                              # no panes vanished on restore
    assert pane_a._toolbar._max.toolTip() == "Maximize pane"


def test_move_to_new_pane_while_maximized(app):
    """Moving an indicator to a new pane while ANOTHER pane is maximized must not corrupt the
    lock or crash.  Only the unrendered pane B is dropped; pane A (the maximized one) stays
    live and the lock must reference it (or be None — never a dangling ref)."""
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")   # pane A — we will maximize this
    b = pc.add_indicator("macd")  # pane B — we will move this to a new pane

    pane_a = a.pane
    assert pane_a is not b.pane   # sanity: two separate panes

    # maximize pane A
    pc._toggle_maximize_pane(pane_a)
    assert pc._maximized_pane is pane_a

    # move the indicator in pane B to a brand-new pane; this unrenders pane B
    # (single-indicator pane -> pane B is deleted), creates pane C for b
    pc.move_indicator(b.uid, "new")

    # _maximized_pane must reference a LIVE pane, never a deleted QWidget
    # (pane A was not touched; pane B was deleted but was not the maximized pane)
    mp = pc._maximized_pane
    live_panes = pc._panes_in_visual_order()
    if mp is not None:
        assert mp in live_panes, (
            "BUG: _maximized_pane references a deleted QWidget after move_indicator('new')"
        )
    # pane A must still be alive (we only unrendered pane B)
    assert pane_a in live_panes, (
        "BUG: pane A was unexpectedly dropped during move_indicator('new') on pane B"
    )
    # lock should still point to pane A (it was not touched)
    assert pc._maximized_pane is pane_a, (
        "BUG: _maximized_pane was unexpectedly cleared when an unrelated pane was moved"
    )

    # structural check: price pane + pane A + new pane C = 3 widgets in splitter
    assert split.count() == 3

    # alignment: exactly one pane owns the bottom time axis — the lowest one
    bottom_owners = [p for p in live_panes if p.getAxis("bottom").isVisible()]
    assert len(bottom_owners) == 1, (
        f"Expected exactly 1 bottom-axis owner, got {len(bottom_owners)}"
    )
    assert bottom_owners[0] is live_panes[-1], (
        "Bottom time axis must be on the lowest pane"
    )


# --- PHASE A: cross-pane crosshair --------------------------------------------------------------
def test_tag_qss_is_module_const_and_reused(app):
    from vike_trader_app.ui import chart as chart_mod
    # the inline tag style is now a module constant…
    assert isinstance(chart_mod._TAG_QSS, str)
    assert "border-radius:2px" in chart_mod._TAG_QSS
    assert "font-size:10px" in chart_mod._TAG_QSS
    # …and the price-pane tags are styled from it (no behaviour change)
    pc, _ = _chart(app)
    assert pc._cx_price_tag.styleSheet() == chart_mod._TAG_QSS
    assert pc._cx_time_tag.styleSheet() == chart_mod._TAG_QSS


def test_oscillator_pane_has_crosshair_items_and_signals(app):
    import pyqtgraph as pg
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    # crosshair line: a hidden, vertical, non-bound InfiniteLine
    assert isinstance(pane._cx_v, pg.InfiniteLine)
    assert pane._cx_v.angle == 90
    assert pane._cx_v.isVisible() is False
    # value + time tags: hidden QLabels sharing the module tag style
    from vike_trader_app.ui import chart as chart_mod
    assert isinstance(pane._cx_val_tag, QtWidgets.QLabel)
    assert isinstance(pane._cx_time_tag, QtWidgets.QLabel)
    assert pane._cx_val_tag.styleSheet() == chart_mod._TAG_QSS
    assert pane._cx_time_tag.styleSheet() == chart_mod._TAG_QSS
    assert pane._cx_val_tag.isHidden() and pane._cx_time_tag.isHidden()
    # new fan-out signals exist
    seen = {"moved": [], "left": 0}
    pane.crosshairMoved.connect(lambda x: seen["moved"].append(x))
    pane.crosshairLeft.connect(lambda: seen.__setitem__("left", seen["left"] + 1))
    pane.crosshairMoved.emit(7.0)
    pane.crosshairLeft.emit()
    assert seen["moved"] == [7.0] and seen["left"] == 1


def test_pane_set_and_clear_crosshair(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 140)
    # set: line snaps to the rounded bar index, shows, value tag un-hidden on the right edge
    # Use bar 20 (well past RSI's 14-bar warm-up so the series has a real value there)
    pane.set_crosshair_x(20.4)
    assert pane._cx_v.isVisible() is True
    assert pane._cx_v.value() == 20          # snapped to round(x)
    assert pane._cx_bar == 20
    assert not pane._cx_val_tag.isHidden()   # value tag shown (offscreen -> use not isHidden())
    # value tag sits at the right edge of the pane (over the axis, TV-style): right edge is
    # within a few pixels of the pane width (not inboard by axis_w).
    tag_right = pane._cx_val_tag.x() + pane._cx_val_tag.width()
    assert tag_right <= pane.width()         # never overflows the pane
    assert tag_right >= pane.width() - pane._cx_val_tag.width() - 2  # anchored near the right edge
    # repeated set at the SAME bar is throttled: bar cache unchanged, line stays put
    pane.set_crosshair_x(20.0)
    assert pane._cx_bar == 20 and pane._cx_v.value() == 20
    # a new bar moves the line
    pane.set_crosshair_x(30.0)
    assert pane._cx_v.value() == 30 and pane._cx_bar == 30
    # clear: line + both tags hidden, bar cache reset
    pane.set_time_tag("06-04 12:00", scene_x=50.0)
    assert not pane._cx_time_tag.isHidden()
    pane.clear_crosshair()
    assert pane._cx_v.isVisible() is False
    assert pane._cx_val_tag.isHidden() and pane._cx_time_tag.isHidden()
    assert pane._cx_bar is None


def test_pane_mouse_move_emits_moved_and_left(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    moved, left = [], []
    pane.crosshairMoved.connect(moved.append)
    pane.crosshairLeft.connect(lambda: left.append(1))
    vb = pane.getViewBox()
    # hover INSIDE the pane viewbox -> crosshairMoved(bar-index x)
    inside = vb.sceneBoundingRect().center()
    pane._on_pane_mouse_moved(inside)
    assert len(moved) == 1
    want_x = vb.mapSceneToView(inside).x()
    assert moved[0] == pytest.approx(want_x)
    # hover OUTSIDE the pane viewbox -> crosshairLeft
    outside = QtCore.QPointF(vb.sceneBoundingRect().right() + 9999,
                             vb.sceneBoundingRect().center().y())
    pane._on_pane_mouse_moved(outside)
    assert left == [1]


def test_pane_leave_event_emits_crosshair_left_and_keeps_toolbar(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 120)
    pane.enterEvent(None)        # toolbar shown
    assert not pane._toolbar.isHidden()
    left = []
    pane.crosshairLeft.connect(lambda: left.append(1))
    pane.leaveEvent(None)        # leaving the pane clears the crosshair AND hides the toolbar
    assert left == [1]
    assert pane._toolbar.isHidden()   # Phase-2 toolbar logic preserved


def test_price_set_crosshair_fans_to_all_panes(app):
    pc, split = _chart(app)
    split.resize(900, 800)
    split.show()
    app.processEvents()
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._set_crosshair_x(18.6)
    # price-pane vertical line + every pane's vertical line snap to the SAME bar index
    assert pc._cx_v.value() == 19
    assert a.pane._cx_v.value() == 19 and a.pane._cx_v.isVisible() is True
    assert b.pane._cx_v.value() == 19 and b.pane._cx_v.isVisible() is True
    # the lowest pane (visual order) carries the time tag; non-lowest panes do not
    panes = pc._panes_in_visual_order()
    assert not panes[-1]._cx_time_tag.isHidden()
    assert panes[0]._cx_time_tag.isHidden()


def test_price_clear_crosshair_clears_everything(app):
    pc, split = _chart(app)
    split.resize(900, 800)
    split.show()
    app.processEvents()
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._set_crosshair_x(15.0)
    assert pc._cx_v.isVisible() is True and a.pane._cx_v.isVisible() is True
    pc._clear_crosshair()
    assert pc._cx_v.isVisible() is False
    assert pc._cx_h.isVisible() is False
    assert pc._cx_price_tag.isHidden() and pc._cx_time_tag.isHidden()
    assert a.pane._cx_v.isVisible() is False and b.pane._cx_v.isVisible() is False
    assert a.pane._cx_val_tag.isHidden() and a.pane._cx_time_tag.isHidden()


def test_price_set_crosshair_no_panes_uses_own_time_tag(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    pc._set_crosshair_x(10.0)
    # no panes -> price chart owns the bottom axis, so its OWN time tag is used
    assert pc._cx_v.value() == 10
    assert pc._cx_time_tag.isHidden() is False


def test_price_leave_event_clears_crosshair(app):
    pc, split = _chart(app)
    split.resize(900, 800)
    split.show()
    app.processEvents()
    a = pc.add_indicator("rsi")
    # show the crosshair (e.g. a hover settled mid-chart) then leave the widget
    pc._set_crosshair_x(22.0)
    assert pc._cx_v.isVisible() is True and a.pane._cx_v.isVisible() is True
    pc.leaveEvent(None)
    # leaving the price chart clears the whole cross-pane crosshair (covers the splitter gutter)
    assert pc._cx_v.isVisible() is False
    assert pc._cx_price_tag.isHidden() and pc._cx_time_tag.isHidden()
    assert a.pane._cx_v.isVisible() is False
    assert a.pane._cx_val_tag.isHidden() and a.pane._cx_time_tag.isHidden()


def test_pane_hover_fans_to_price_and_other_panes(app):
    pc, split = _chart(app)
    split.resize(900, 800)
    split.show()
    app.processEvents()
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    # a hover inside pane a is re-emitted by _new_pane's wiring -> _set_crosshair_x fans out
    vb = a.pane.getViewBox()
    inside = vb.sceneBoundingRect().center()
    bar = int(round(vb.mapSceneToView(inside).x()))
    a.pane._on_pane_mouse_moved(inside)
    # price vertical line + the OTHER pane's line snap to the hovered bar
    assert pc._cx_v.value() == bar and pc._cx_v.isVisible() is True
    assert b.pane._cx_v.value() == bar and b.pane._cx_v.isVisible() is True
    # the price-pane horizontal read-out line is NOT shown for a pane-originated hover
    assert pc._cx_h.isVisible() is False
    # leaving the pane fans a clear back to the price chart -> everything hidden
    outside = QtCore.QPointF(vb.sceneBoundingRect().right() + 9999,
                             vb.sceneBoundingRect().center().y())
    a.pane._on_pane_mouse_moved(outside)
    assert pc._cx_v.isVisible() is False
    assert a.pane._cx_v.isVisible() is False and b.pane._cx_v.isVisible() is False


# --- Phase A review fixes -------------------------------------------------------------------

def test_price_set_crosshair_throttled_same_bar(app):
    """_set_crosshair_x must skip fan-out work (setPos, time-tag, pane fan) when the bar index
    is unchanged from the last call.  A sub-pixel move within bar 25 must be a no-op; a move
    to bar 26 must redo the work."""
    pc, split = _chart(app)
    split.resize(900, 800)
    split.show()
    app.processEvents()
    pc.add_indicator("rsi")   # so at least one pane exists (exercises the fan-out path)

    # Instrument _cx_v.setPos to count calls
    call_count = []
    real_setPos = pc._cx_v.setPos
    pc._cx_v.setPos = lambda v: (call_count.append(v), real_setPos(v))

    # First call: bar 25 — must do full work
    pc._set_crosshair_x(25.0)
    assert pc._cx_v.isVisible() is True
    assert pc._cx_bar == 25
    count_after_first = len(call_count)
    assert count_after_first == 1, f"Expected 1 setPos call after first move, got {count_after_first}"

    # Second call: 25.2 — still bar 25 (round(25.2) == 25) — must be throttled
    pc._set_crosshair_x(25.2)
    assert pc._cx_bar == 25                      # bar unchanged
    count_after_second = len(call_count)
    assert count_after_second == count_after_first, (
        f"setPos was called again for same bar (sub-pixel move): "
        f"count went from {count_after_first} to {count_after_second}"
    )

    # Third call: bar 26 — new bar, must redo the work
    pc._set_crosshair_x(26.0)
    assert pc._cx_bar == 26                      # bar advanced
    count_after_third = len(call_count)
    assert count_after_third > count_after_second, (
        f"setPos was NOT called for a new bar: count stayed at {count_after_second}"
    )

    # After a clear, the same bar must NOT be throttled (re-show after leave)
    pc._clear_crosshair()
    assert pc._cx_bar is None                    # cache reset
    count_before_reshown = len(call_count)
    pc._set_crosshair_x(26.0)                   # same bar 26, but after clear
    assert len(call_count) > count_before_reshown, (
        "After _clear_crosshair the same bar must NOT be throttled (re-show after leave)"
    )


def test_pane_crosshair_ignores_bounds(app):
    """OscillatorPane._cx_v must be added with ignoreBounds=True so positioning the crosshair
    at an out-of-range bar (e.g. 100000) cannot expand the pane's x view-range."""
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ind = pc.add_indicator("rsi")
    pane = ind.pane

    vb = pane.getViewBox()
    # record the x-range before any crosshair move
    x_range_before = vb.viewRange()[0]

    # drive the crosshair to a far-out-of-range bar index
    pane.set_crosshair_x(100000)
    x_range_after = vb.viewRange()[0]

    assert x_range_after == pytest.approx(x_range_before, abs=1e-3), (
        f"Crosshair at bar 100000 expanded the pane x-range from {x_range_before} to "
        f"{x_range_after}; ignoreBounds=True is broken or missing"
    )


def test_pane_value_tag_anchored_at_right_edge(app):
    """OscillatorPane._cx_val_tag must be anchored at the right edge of the pane (over the
    right axis, like the price-pane's price tag) — NOT inboard by axis_w.  This locks the
    TV-consistent anchoring introduced by the Fix-3 change."""
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 140)

    # Use bar 20 (past RSI warm-up) so _series_value_at returns a real value
    pane.set_crosshair_x(20.0)

    assert not pane._cx_val_tag.isHidden(), "value tag must be shown after set_crosshair_x"

    tag = pane._cx_val_tag
    tag_right = tag.x() + tag.width()
    pane_width = pane.width()

    # Right edge must be within 1px of pane_width - 1 (the formula is width - tag.width() - 1)
    expected_right = pane_width - 1
    assert abs(tag_right - expected_right) <= 1, (
        f"Value tag right edge ({tag_right}) is not at pane right edge ({expected_right}); "
        "Fix-3 TV-consistent anchoring may have regressed"
    )


# --- PHASE B: source helpers -----------------------------------------------------------------
def test_is_source_selectable_count_is_53(app):
    import vike_trader_app.core.indicators  # noqa: F401 - populate REGISTRY
    from vike_trader_app.core.indicators import base as _base
    from vike_trader_app.ui.chart import is_source_selectable
    sel = [s for s in _base.list_indicators() if is_source_selectable(s)]
    assert len(sel) == 53
    assert all(s.inputs[0] == "close" for s in sel)


def test_is_source_selectable_gates_correctly(app):
    from vike_trader_app.core.indicators import base as _base
    from vike_trader_app.ui.chart import is_source_selectable
    assert is_source_selectable(_base.get("rsi")) is True
    assert is_source_selectable(_base.get("sma")) is True
    assert is_source_selectable(_base.get("bollinger")) is True   # single close, multi-output
    assert is_source_selectable(_base.get("stochastic")) is False  # high/low/close
    assert is_source_selectable(_base.get("obv")) is False         # close/volume
    assert is_source_selectable(_base.get("volume_osc")) is False  # volume
    assert is_source_selectable(_base.get("engulfing")) is False   # open/high/low/close
    assert is_source_selectable(_base.get("ratio")) is False       # close/benchmark


def test_source_options_are_the_eight_tv_sources(app):
    from vike_trader_app.ui.chart import _SOURCE_OPTIONS
    assert _SOURCE_OPTIONS == ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"]


def test_source_series_raw_and_derived_math(app):
    from vike_trader_app.ui.chart import _source_series
    data = {
        "open": [10.0, 20.0], "high": [12.0, 24.0],
        "low": [8.0, 16.0], "close": [11.0, 22.0], "volume": [0, 0],
    }
    assert _source_series(data, "open") == [10.0, 20.0]
    assert _source_series(data, "high") == [12.0, 24.0]
    assert _source_series(data, "low") == [8.0, 16.0]
    assert _source_series(data, "close") == [11.0, 22.0]
    # hl2 = (h+l)/2
    assert _source_series(data, "hl2") == [(12.0 + 8.0) / 2, (24.0 + 16.0) / 2]
    # hlc3 = (h+l+c)/3
    assert _source_series(data, "hlc3") == [(12.0 + 8.0 + 11.0) / 3, (24.0 + 16.0 + 22.0) / 3]
    # ohlc4 = (o+h+l+c)/4
    assert _source_series(data, "ohlc4") == [(10.0 + 12.0 + 8.0 + 11.0) / 4,
                                             (20.0 + 24.0 + 16.0 + 22.0) / 4]
    # hlcc4 = (h+l+2c)/4
    assert _source_series(data, "hlcc4") == [(12.0 + 8.0 + 2 * 11.0) / 4,
                                             (24.0 + 16.0 + 2 * 22.0) / 4]
    # unknown source -> close
    assert _source_series(data, "bogus") == [11.0, 22.0]


def test_indicator_source_defaults_to_close(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert ind.source == "close"


def test_spec_defaults_includes_close_source(app):
    from vike_trader_app.core.indicators import base as _base
    from vike_trader_app.ui.chart import _Indicator
    params, colors, widths, styles, source = _Indicator.spec_defaults(_base.get("rsi"))
    assert source == "close"


def test_label_appends_non_default_source(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    assert ind.label == "RSI 14"          # default close -> no suffix
    ind.source = "hl2"
    assert ind.label == "RSI 14 (hl2)"    # non-default -> suffix
    ind.source = "close"
    assert ind.label == "RSI 14"          # back to default -> no suffix


def test_compute_remaps_source_hl2_sma(app):
    pc, _ = _chart(app)
    bars = pc._bars
    ind = pc.add_indicator("sma", params={"period": 3})
    # baseline (close-fed) values:
    close_vals = list(ind.series["sma"])
    # switch source to hl2 and recompute:
    ind.source = "hl2"
    pc._compute(ind)
    hl2_vals = list(ind.series["sma"])
    # the two series must DIFFER (hl2 != close for these bars):
    assert hl2_vals != close_vals
    # and match a hand-computed 3-period SMA of hl2 = (high+low)/2:
    hl2 = [(b.high + b.low) / 2 for b in bars]
    p = 3
    expected = [None] * (p - 1) + [
        sum(hl2[i - p + 1:i + 1]) / p for i in range(p - 1, len(hl2))
    ]
    got = ind.series["sma"]
    assert len(got) == len(expected)
    for g, e in zip(got, expected):
        if e is None:
            assert g is None
        else:
            assert g == pytest.approx(e)


def test_compute_default_source_is_byte_identical(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("sma", params={"period": 5})
    before = list(ind.series["sma"])
    # recompute with the (default) close source -> exact same series, no remap overhead path:
    assert ind.source == "close"
    pc._compute(ind)
    after = list(ind.series["sma"])
    assert after == before


def test_compute_remaps_multi_output_bollinger_on_ohlc4(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("bollinger")
    ind.source = "ohlc4"
    pc._compute(ind)
    # all three bands still populated after the single-column swap:
    assert set(ind.series) == {"upper", "mid", "lower"}
    for lbl in ("upper", "mid", "lower"):
        assert _valid({lbl: ind.series[lbl]}) > 0


def test_settings_shows_source_combo_for_selectable(app):
    pc, _ = _chart(app)
    for nm in ("rsi", "sma"):
        ind = pc.add_indicator(nm)
        dlg = _IndicatorSettings(ind)
        assert dlg._source_combo is not None
        # the eight TV sources, current = close (default):
        keys = [dlg._source_combo.itemData(i) for i in range(dlg._source_combo.count())]
        assert keys == ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"]
        assert dlg._source_combo.currentData() == "close"


def test_settings_hides_source_combo_when_not_selectable(app):
    pc, _ = _chart(app)
    for nm in ("stochastic", "obv", "volume_osc", "engulfing"):
        ind = pc.add_indicator(nm)
        if ind is None:
            continue
        dlg = _IndicatorSettings(ind)
        assert dlg._source_combo is None


def test_settings_source_combo_reflects_current_source(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    ind.source = "hl2"
    dlg = _IndicatorSettings(ind)
    assert dlg._source_combo.currentData() == "hl2"


def test_settings_reset_defaults_resets_source_to_close(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    ind.source = "ohlc4"
    dlg = _IndicatorSettings(ind)
    assert dlg._source_combo.currentData() == "ohlc4"
    dlg._reset_defaults()
    assert dlg._source_combo.currentData() == "close"


def test_settings_emits_source_on_ok(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    from vike_trader_app.ui.chart import _SOURCE_OPTIONS
    dlg._source_combo.setCurrentIndex(_SOURCE_OPTIONS.index("hl2"))
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source: got.update(source=source)
    )
    dlg._accept()
    assert got["source"] == "hl2"


def test_apply_edit_assigns_source_and_recomputes(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("sma", params={"period": 3})
    close_vals = list(ind.series["sma"])
    # drive the full edit path with a non-default source:
    pc._apply_edit(ind.uid, dict(ind.params), list(ind.colors), source="hl2")
    assert ind.source == "hl2"
    assert list(ind.series["sma"]) != close_vals       # recomputed against hl2
    assert ind.label == "SMA 3 (hl2)"                  # legend reflects the source


def test_apply_edit_default_source_unset_preserves(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    ind.source = "ohlc4"
    # omit `source` -> _UNSET -> ind.source must be preserved (not reset to close):
    pc._apply_edit(ind.uid, dict(ind.params), list(ind.colors))
    assert ind.source == "ohlc4"


def test_clone_carries_non_default_source(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("sma", params={"period": 4})
    ind.source = "hlc3"
    pc._compute(ind)  # ensure ind reflects the edited source before cloning
    clone = pc.clone_indicator(ind.uid)
    assert clone is not None
    assert clone.uid != ind.uid
    assert clone.source == "hlc3"
    # the clone's series must match the source-fed original (same params, same source):
    assert list(clone.series["sma"]) == list(ind.series["sma"])
    assert clone.label == "SMA 4 (hlc3)"


# --- PHASE C: Threshold bands -----------------------------------------------------------------
def test_indicator_bands_table_canonical_values(app):
    from vike_trader_app.ui.chart import _INDICATOR_BANDS
    # oscillators with explicit upper/middle/lower or two-line bands
    assert _INDICATOR_BANDS["rsi"] == [("Upper", 70.0), ("Middle", 50.0), ("Lower", 30.0)]
    assert _INDICATOR_BANDS["stochastic"] == [("Upper", 80.0), ("Lower", 20.0)]
    assert _INDICATOR_BANDS["stochf"] == [("Upper", 80.0), ("Lower", 20.0)]
    assert _INDICATOR_BANDS["stochrsi"] == [("Upper", 80.0), ("Lower", 20.0)]
    # williams_r native range is [-100, 0] -> -20 / -80 (NOT 20/80)
    assert _INDICATOR_BANDS["williams_r"] == [("Upper", -20.0), ("Lower", -80.0)]
    assert _INDICATOR_BANDS["cci"] == [("Upper", 100.0), ("Middle", 0.0), ("Lower", -100.0)]
    assert _INDICATOR_BANDS["ultosc"] == [("Upper", 70.0), ("Lower", 30.0)]
    assert _INDICATOR_BANDS["aroon"] == [("Upper", 70.0), ("Lower", 30.0)]
    assert _INDICATOR_BANDS["adx"] == [("Threshold", 25.0)]
    assert _INDICATOR_BANDS["adxr"] == [("Threshold", 25.0)]
    assert _INDICATOR_BANDS["connors_rsi"] == [("Upper", 90.0), ("Lower", 10.0)]
    assert _INDICATOR_BANDS["zscore"] == [("Upper", 2.0), ("Middle", 0.0), ("Lower", -2.0)]
    assert _INDICATOR_BANDS["spread_zscore"] == [("Upper", 2.0), ("Middle", 0.0), ("Lower", -2.0)]
    # 0-centerline family -> a single Zero line
    for name in ("macd", "ppo", "apo", "mom", "roc", "rocp", "ao", "ac", "dpo", "trix",
                 "tsi", "smi_ergodic", "cmo", "elder_ray", "kvo", "adosc", "net_volume", "bop"):
        assert _INDICATOR_BANDS[name] == [("Zero", 0.0)], name
    # mfi is NOT registered, so it must NOT be in the table
    assert "mfi" not in _INDICATOR_BANDS
    # overlays / unlisted indicators have no bands
    assert "ema" not in _INDICATOR_BANDS and "sma" not in _INDICATOR_BANDS


def test_indicator_bands_seed_and_colors(app):
    from vike_trader_app.ui import theme
    from vike_trader_app.ui.chart import _Indicator
    import vike_trader_app.core.indicators  # noqa: F401 - populate REGISTRY
    from vike_trader_app.core.indicators import base
    # rsi -> 3 bands, mutable per-instance copy (not the shared table list)
    rsi = _Indicator("rsi", base.get("rsi"), {"period": 14}, "oscillator")
    assert [(lbl, val) for lbl, val in rsi.bands] == [("Upper", 70.0), ("Middle", 50.0), ("Lower", 30.0)]
    assert rsi.band_colors == [theme.TEXT3, theme.TEXT3, theme.TEXT3]
    rsi.bands[0][1] = 80.0                       # editing the instance copy ...
    rsi2 = _Indicator("rsi", base.get("rsi"), {"period": 14}, "oscillator")
    assert rsi2.bands[0][1] == 70.0              # ... must NOT mutate the canonical seed
    # macd -> a single 0 band
    macd = _Indicator("macd", base.get("macd"), {}, "oscillator")
    assert [(lbl, val) for lbl, val in macd.bands] == [("Zero", 0.0)]
    assert macd.band_colors == [theme.TEXT3]
    # overlay (ema) -> no bands
    ema = _Indicator("ema", base.get("ema"), {"period": 20}, "overlay")
    assert ema.bands == [] and ema.band_colors == []
    # band_defaults helper returns the canonical seed (label, value) pairs
    assert _Indicator.band_defaults("rsi") == [("Upper", 70.0), ("Middle", 50.0), ("Lower", 30.0)]
    assert _Indicator.band_defaults("ema") == []


def test_oscillator_builds_band_lines_out_of_curves(app):
    import pyqtgraph as pg
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")          # 3 bands
    pane = ind.pane
    lines = pane._band_lines[ind.uid]
    assert len(lines) == 3
    assert all(isinstance(ln, pg.InfiniteLine) and ln.angle == 0 for ln in lines)
    assert [ln.value() for ln in lines] == [70.0, 50.0, 30.0]
    # band lines are dashed and NOT in _curves (so reveal/crosshair never see them)
    assert all(ln.pen.style() == QtCore.Qt.DashLine for ln in lines)
    curve_items = [c for cs in pane._curves.get(ind.uid, {}).values() for c in [cs]]
    assert all(ln not in curve_items for ln in lines)
    # the band InfiniteLines are actually added to the pane's scene
    assert all(ln.scene() is pane.scene() for ln in lines)
    # an overlay merged in has no band lines; remove drops the lines
    pane.remove_ind(ind.uid)
    assert ind.uid not in pane._band_lines


def test_oscillator_macd_single_zero_band(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("macd")
    lines = ind.pane._band_lines[ind.uid]
    assert len(lines) == 1 and lines[0].value() == 0.0


def test_reveal_unions_band_values_extend_only(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.reveal(75)
    lo, hi = pane.getViewBox().viewRange()[1]
    # every band value sits inside the (padded) y-range
    for _lbl, val in ind.bands:
        assert lo <= val <= hi, (val, lo, hi)
    # the series is still inside the range too (union is extend-only, never overriding the data)
    ser = ind.series["rsi"]
    vals = [v for v in ser[:76] if v is not None]
    assert lo <= min(vals) and max(vals) <= hi


def test_reveal_band_below_series_extends_low(app):
    # williams_r bands are negative (-20/-80) and its series is in [-100, 0]; the -80 guide must
    # widen the low end so it stays on-screen.
    pc, _ = _chart(app)
    ind = pc.add_indicator("williams_r")
    pane = ind.pane
    pane.reveal(75)
    lo, _hi = pane.getViewBox().viewRange()[1]
    assert lo <= -80.0
