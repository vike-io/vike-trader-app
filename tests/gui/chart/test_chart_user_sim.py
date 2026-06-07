"""End-to-end "real user simulation" for the price chart.

This drives the REAL ``PriceChart`` the way a charting user would — it loads bars, adds an
overlay, an oscillator, and a candlestick pattern from the catalog, opens the real
``_IndicatorSettings`` dialog and clicks its "Ok" button to change Period + Source + the
Smoothing Line (EMA)/Length, reloads the chart with a different bar set / timeframe and asserts
the indicators persist + recompute, exercises the TradingView navigation (zoom in/out, scroll,
reset), and finally removes an indicator. Assertions are on OBSERVABLE state: ``chart._indicators``,
the created curves/pane/scatter render handles, the recomputed ``ind.series``, and the visible
x-span — not internals.

NO network (synthetic ``Bar`` lists), NO modal ``exec()`` (the settings dialog is built directly
and its Ok button is clicked, with ``applied`` wired to the chart's ``_apply_edit`` exactly like
``edit_indicator`` does), everything on the main thread.
"""

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from vike_trader_app.core.model import Bar  # noqa: E402
from vike_trader_app.ui.chart import (  # noqa: E402
    _IndicatorSettings,
    _MA_SERIES_KEY,
    OscillatorPane,
    PriceChart,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _bars(n=120, *, phase=0.0, drift=0.10):
    """Synthetic OHLCV: a sine wave on a slow uptrend with alternating-body candles so the
    candlestick-pattern detector (engulfing) actually fires a few times — a realistic feed."""
    out = []
    for i in range(n):
        c = 100 + 8 * math.sin((i + phase) / 5.0) + i * drift
        o = c - (1.6 if i % 3 == 0 else -1.3)  # alternate body direction -> engulfing setups
        hi = max(o, c) + 1.0
        lo = min(o, c) - 1.0
        out.append(Bar(ts=i * 60_000, open=o, high=hi, low=lo, close=c, volume=1_000 + i))
    return out


def _chart(app):
    """A PriceChart wired to a vertical splitter pane-host, mounted + sized like app.py does."""
    pc = PriceChart()
    split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
    split.addWidget(pc)
    pc.set_pane_host(split)
    pc.set_data(_bars(), [])
    split.resize(900, 640)
    return pc, split


def _valid(series):
    """Count of non-None samples in the first output series (warm-up gaps are None)."""
    return sum(1 for v in next(iter(series.values())) if v is not None)


def _span(pc):
    (x0, x1), _ = pc.getViewBox().viewRange()
    return x1 - x0


def test_chart_full_user_journey(app, monkeypatch):
    """One long user session: load -> add 3 indicator kinds -> edit RSI via the real dialog ->
    reload (set_data) -> change timeframe -> navigate -> remove. Asserts observable state at
    every step."""
    pc, split = _chart(app)

    # --- 1) A user loads bars: the candles + price axis are populated ---------------------
    assert len(pc._bars) == 120
    assert not pc._indicators                  # a fresh chart has no indicators
    assert split.count() == 1                  # only the price pane so far

    # --- 2) Add an OVERLAY (EMA): rides the price scale, gets a curve, no pane ------------
    ema = pc.add_indicator("ema")
    assert ema is not None
    assert ema.kind == "overlay" and ema.uid in pc._indicators
    assert ema.curves and ema.pane is None and ema.scatter is None
    assert split.count() == 1                  # overlays do NOT create a pane
    ema_curve = next(iter(ema.curves.values()))
    assert ema_curve.getData()[0] is not None and len(ema_curve.getData()[0]) > 0  # rendered

    # --- 3) Add an OSCILLATOR (RSI): spawns its own sub-pane with a curve -----------------
    rsi = pc.add_indicator("rsi")
    assert rsi is not None
    assert rsi.kind == "oscillator" and rsi.uid in pc._indicators
    assert isinstance(rsi.pane, OscillatorPane)
    assert split.count() == 2                  # price + the new oscillator pane
    assert rsi.uid in rsi.pane.uids
    rsi_curve = next(iter(rsi.pane._curves[rsi.uid].values()))
    assert len(rsi_curve.getData()[0]) > 0     # the RSI line actually drew points

    # --- 4) Add a PATTERN (engulfing): bar markers via a scatter on the price pane --------
    eng = pc.add_indicator("engulfing")
    assert eng is not None
    assert eng.kind == "pattern" and eng.scatter is not None
    fired = sum(1 for v in next(iter(eng.series.values())) if v)
    assert fired > 0                           # our synthetic feed triggers some engulfings
    assert len(eng.scatter.data) == fired      # one scatter spot per fired bar

    assert len(pc._indicators) == 3

    # --- 5) Open the REAL settings dialog for RSI and click "Ok" --------------------------
    # Build the dialog exactly as edit_indicator() does, but click the Ok QPushButton instead of
    # calling exec() (a modal would hang headless). Wire `applied` -> _apply_edit like the app.
    dlg = _IndicatorSettings(rsi, pc)
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source, bands, u=rsi.uid: pc._apply_edit(
            u, params, colors, widths=widths, styles=styles, intervals=intervals,
            source=source, bands=bands,
        )
    )
    before_valid = _valid(rsi.series)
    # change Period (14 -> 5: a shorter window = fewer warm-up gaps -> a different valid count)
    dlg._param_widgets["period"].setValue(5)
    # change Source (close -> hl2)
    src_idx = next(i for i in range(dlg._source_combo.count())
                   if dlg._source_combo.itemData(i) == "hl2")
    dlg._source_combo.setCurrentIndex(src_idx)
    # change the Smoothing Line to EMA, length 8 (TradeLocker "Smoothing Line")
    sm_idx = next(i for i in range(dlg._smooth_combo.count())
                  if dlg._smooth_combo.itemData(i) == "ema")
    dlg._smooth_combo.setCurrentIndex(sm_idx)
    dlg._smooth_len_spin.setValue(8)

    ok_btns = [b for b in dlg.findChildren(QtWidgets.QPushButton) if b.text() == "Ok"]
    assert len(ok_btns) == 1                    # the dialog really has an Ok button to click
    ok_btns[0].click()                          # <-- the user clicks Ok

    edited = pc._indicators[rsi.uid]
    assert edited.params["period"] == 5         # param applied
    assert edited.source == "hl2"               # source applied
    assert edited.smooth_type == "ema" and edited.smooth_len == 8  # smoothing applied
    assert edited.label == "RSI 5 (hl2)"        # legend reflects the edit (param + source suffix)
    assert _valid(edited.series) != before_valid  # series ACTUALLY recomputed
    # the smoothing MA was added as an extra series AND an extra pane curve, keyed last
    assert _MA_SERIES_KEY in edited.series
    assert _MA_SERIES_KEY in edited.pane._curves[edited.uid]
    assert len(edited.pane._curves[edited.uid][_MA_SERIES_KEY].getData()[0]) > 0

    # --- 6) RELOAD: new bar set (a different symbol/interval) -> indicators PERSIST -------
    # set_data() runs _recompute_indicators(): overlay + oscillator + pattern survive & recompute.
    pc.set_data(_bars(60, phase=3.0, drift=0.25), [])
    assert len(pc._bars) == 60
    assert len(pc._indicators) == 3             # all three kept across the reload (TV-style)
    assert ema.uid in pc._indicators and rsi.uid in pc._indicators and eng.uid in pc._indicators
    assert split.count() == 2                   # the RSI pane is still mounted
    # recomputed onto the NEW (shorter) bars: every series is now length 60
    assert all(len(s) == 60 for s in ema.series.values())
    assert all(len(s) == 60 for s in rsi.series.values())
    assert all(len(s) == 60 for s in eng.series.values())
    # the edited RSI kept its settings through the reload and re-rendered its pane curve
    assert rsi.params["period"] == 5 and rsi.source == "hl2" and rsi.smooth_type == "ema"
    assert len(next(iter(rsi.pane._curves[rsi.uid].values())).getData()[0]) > 0

    # --- 7) CHANGE TIMEFRAME: indicators stay shown + the pane keeps its axis -------------
    pc.set_timeframe("5m")
    assert all(i.shown for i in pc._indicators.values())  # default = visible on all timeframes
    assert rsi.pane.getAxis("bottom").isVisible() is True  # lowest pane still owns the time axis

    # --- 8) NAVIGATE: zoom in/out, scroll, reset -> the visible x-span changes/returns ----
    base = _span(pc)
    pc.nav_zoom(0.5)                            # zoom IN -> a narrower window
    zoomed_in = _span(pc)
    assert zoomed_in < base
    pc.nav_zoom(2.0)                            # zoom OUT -> back roughly to base
    assert _span(pc) > zoomed_in
    pc.nav_zoom(0.5)                            # zoom in again so a scroll is observable
    (x0_before, x1_before), _ = pc.getViewBox().viewRange()
    pc.nav_scroll(-0.5)                         # scroll BACK -> the window shifts left
    (x0_after, x1_after), _ = pc.getViewBox().viewRange()
    assert x0_after < x0_before and x1_after < x1_before
    pc._follow = False                          # pretend the user panned away from the live edge
    pc.nav_reset()                              # reset -> snap back to the default follow window
    assert pc._follow is True
    assert _span(pc) == pytest.approx(base, rel=0.05)  # span returns to the default window

    # --- 9) REMOVE indicators: each disappears + frees its render handles -----------------
    pc.remove_indicator(eng.uid)               # pattern -> scatter removed
    assert eng.uid not in pc._indicators
    pc.remove_indicator(rsi.uid)               # oscillator -> pane dropped
    assert rsi.uid not in pc._indicators
    assert split.count() == 1                   # back to just the price pane
    pc.remove_indicator(ema.uid)               # overlay gone
    assert ema.uid not in pc._indicators
    assert not pc._indicators                   # the chart is clean again


def test_settings_dialog_cancel_leaves_indicator_untouched(app):
    """A user opens Settings, tweaks fields, then clicks Cancel: nothing applies (Cancel must NOT
    emit `applied`)."""
    pc, _ = _chart(app)
    rsi = pc.add_indicator("rsi")
    before_period = rsi.params["period"]
    before_source = rsi.source

    dlg = _IndicatorSettings(rsi, pc)
    applied_calls = []
    dlg.applied.connect(lambda *a: applied_calls.append(a))
    dlg._param_widgets["period"].setValue(3)            # user fiddles...
    src_idx = next(i for i in range(dlg._source_combo.count())
                   if dlg._source_combo.itemData(i) == "high")
    dlg._source_combo.setCurrentIndex(src_idx)

    cancel = [b for b in dlg.findChildren(QtWidgets.QPushButton) if b.text() == "Cancel"]
    assert len(cancel) == 1
    cancel[0].click()                                    # ...then bails out

    assert applied_calls == []                            # Cancel never emits
    assert rsi.params["period"] == before_period          # indicator untouched
    assert rsi.source == before_source


def test_reload_with_more_bars_extends_series(app):
    """Reloading with a LONGER feed (like switching to a longer history) recomputes the kept
    indicators onto the new length — the persistence path handles growth as well as shrink."""
    pc, split = _chart(app)
    ema = pc.add_indicator("ema")
    rsi = pc.add_indicator("rsi")
    assert split.count() == 2

    pc.set_data(_bars(200, phase=1.0), [])
    assert len(pc._bars) == 200
    assert len(pc._indicators) == 2
    assert all(len(s) == 200 for s in ema.series.values())
    assert all(len(s) == 200 for s in rsi.series.values())
    # the oscillator pane re-revealed onto the longer feed
    assert len(next(iter(rsi.pane._curves[rsi.uid].values())).getData()[0]) > 0
