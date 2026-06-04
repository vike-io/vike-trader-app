# Chart Indicators TV-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring on-chart indicators to TradingView / TradeLocker parity in three areas — time alignment (pixel-aligned panes + bottom time axis), pane management (hover toolbar: move up/down, maximize, delete), and a TV-style settings dialog (per-output line width/style, Visibility tab, Defaults).

**Architecture:** All changes live in `src/vike_trader_app/ui/chart.py` (no `app.py`, no registry change for CORE scope). Phase 1 makes every pane's right axis a shared width (computed synchronously from font metrics, not the stale `getAxis().width()`) and moves the time axis to the lowest pane via per-pane bottom `TimeAxis` (approach B1), orchestrated by `_align_panes()`. Phase 2 adds a per-pane floating hover toolbar and maximize/restore. Phase 3 deepens the settings dialog. Build order is 1 → 2 → 3; Phase 2 depends on Phase 1's `_align_panes`/`_panes_in_visual_order`.

**Tech Stack:** PySide6 (Qt), pyqtgraph 0.14.0, pytest (offscreen via `QT_QPA_PLATFORM`).

**Spec:** `docs/superpowers/specs/2026-06-04-chart-indicators-tv-parity-design.md`

**Test invocation (offscreen is set inside the test file):**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```

---

## Phase 1 — Time alignment

### Task 1: `_panes_in_visual_order()` helper

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` (add method on `PriceChart`, near `_osc_panes` at line 1611)
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_panes_in_visual_order_matches_splitter tests/test_chart_indicators.py::test_panes_in_visual_order_differs_from_osc_after_drag -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_panes_in_visual_order'`.

- [ ] **Step 3: Minimal implementation**

Insert `_panes_in_visual_order` immediately before `_osc_panes` (current line 1611). Current code:

```python
    def _osc_panes(self):
        seen, panes = set(), []
        for i in self._indicators.values():
            if i.pane is not None and id(i.pane) not in seen:
                seen.add(id(i.pane))
                panes.append(i.pane)
        return panes
```

New code (helper added above; `_osc_panes` unchanged):

```python
    def _panes_in_visual_order(self):
        """Oscillator panes in top-to-bottom splitter order (NOT dict-insertion order).
        Use this everywhere pane *order* matters — the bottom time axis and shared
        axis width key off the lowest pane, which `_osc_panes()` (dict order) can't track
        after a drag/reorder."""
        host = self._pane_host
        if host is None:
            return []
        return [host.widget(i) for i in range(1, host.count())
                if isinstance(host.widget(i), OscillatorPane)]

    def _osc_panes(self):
        seen, panes = set(), []
        for i in self._indicators.values():
            if i.pane is not None and id(i.pane) not in seen:
                seen.add(id(i.pane))
                panes.append(i.pane)
        return panes
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_panes_in_visual_order_matches_splitter tests/test_chart_indicators.py::test_panes_in_visual_order_differs_from_osc_after_drag -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add _panes_in_visual_order() splitter-order pane helper"
```

---

### Task 2: `_axis_natural_width(axis)` synchronous width from current tick strings

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` (add method on `PriceChart`, after `_panes_in_visual_order`)
- Test: `tests/test_chart_indicators.py` (append)

Rationale: `AxisItem.width()` is stale/zero before a paint pass (verified: returns `35.0` while the real natural width is `77`). Derive it synchronously from `QFontMetrics` over the axis's **current** tick strings, mirroring pyqtgraph's own `_updateWidth`: `textWidth + style['tickTextOffset'][0] + max(0, style['tickLength'])`.

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_axis_natural_width_exceeds_pyqtgraph_stale_width tests/test_chart_indicators.py::test_axis_natural_width_price_wider_than_oscillator -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_axis_natural_width'`.

- [ ] **Step 3: Minimal implementation**

Insert after the `_panes_in_visual_order` method added in Task 1 (before `_osc_panes`). Current code (the start of `_osc_panes`, the anchor):

```python
    def _osc_panes(self):
        seen, panes = set(), []
```

New code (insert the method ahead of `_osc_panes`):

```python
    def _axis_natural_width(self, axis) -> float:
        """The width a right AxisItem *would* take for its CURRENT tick strings, computed
        synchronously via QFontMetrics — paint-independent so headless tests can assert it
        immediately. Mirrors pyqtgraph's AxisItem._updateWidth:
            textWidth + style['tickTextOffset'][0] + max(0, style['tickLength']).
        Reading axis.width() instead is unsafe: in pyqtgraph 0.14.0 it returns geometry from
        the *last* layout pass, so it is stale (or 0) right after setWidth()."""
        if not axis.isVisible():
            return 0.0
        mn, mx = axis.range
        size = axis.height() or 300
        try:
            levels = axis.tickValues(mn, mx, size)
        except Exception:  # noqa: BLE001 - degenerate range -> no strings to measure
            levels = []
        strings = []
        for spacing, values in levels:
            try:
                strings += [s for s in axis.tickStrings(values, axis.scale, spacing) if s]
            except Exception:  # noqa: BLE001
                pass
        font = axis.style.get("tickFont") or axis.font()
        fm = QtGui.QFontMetrics(font)
        text_w = max((fm.horizontalAdvance(s) for s in strings), default=axis.textWidth)
        return float(text_w + axis.style["tickTextOffset"][0] + max(0, axis.style["tickLength"]))

```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_axis_natural_width_exceeds_pyqtgraph_stale_width tests/test_chart_indicators.py::test_axis_natural_width_price_wider_than_oscillator -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add _axis_natural_width() synchronous tick-string width"
```

---

### Task 3: `_wsyncing` guard field + `_sync_axis_width()` equalizer

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — add `self._wsyncing = False` in `PriceChart.__init__` (after `self._vb2 = None`, line 998); add `_sync_axis_width` after `_axis_natural_width`
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_sync_axis_width_equalizes_above_narrow_pane tests/test_chart_indicators.py::test_sync_axis_width_no_recursion -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_sync_axis_width'`.

- [ ] **Step 3: Minimal implementation**

(3a) Add the guard field. Current `__init__` line 998:

```python
        self._vb2 = None  # secondary ViewBox for overlays pinned to their own scale
```

New:

```python
        self._vb2 = None  # secondary ViewBox for overlays pinned to their own scale
        self._wsyncing = False  # re-entrancy guard for _sync_axis_width (mirrors _fitting)
```

(3b) Add `_sync_axis_width` directly after the `_axis_natural_width` method from Task 2 (still ahead of `_osc_panes`). Anchor — the closing line of `_axis_natural_width`:

```python
        return float(text_w + axis.style["tickTextOffset"][0] + max(0, axis.style["tickLength"]))

```

New code (insert after it):

```python
    def _sync_axis_width(self):
        """Pin every pane's right price axis (and the price chart's) to one shared width so
        plot columns are pixel-aligned in time. Width is the max natural width across axes,
        computed synchronously (no dependence on a pending paint). When there are no panes,
        the price axis is restored to auto so a lone chart isn't stuck at a stale pinned width."""
        if self._wsyncing:
            return
        self._wsyncing = True
        try:
            panes = self._panes_in_visual_order()
            price_ax = self.getAxis("right")
            if not panes:
                price_ax.setWidth(None)  # lone chart -> auto width
                self.getPlotItem().layout.activate()
                return
            axes = [price_ax] + [p.getAxis("right") for p in panes]
            w = int(round(max(self._axis_natural_width(a) for a in axes)))
            for a in axes:
                a.setWidth(w)
            self.getPlotItem().layout.activate()
            for p in panes:
                p.getPlotItem().layout.activate()
        finally:
            self._wsyncing = False
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_sync_axis_width_equalizes_above_narrow_pane tests/test_chart_indicators.py::test_sync_axis_width_no_recursion -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add _sync_axis_width() equalizer with _wsyncing guard"
```

---

### Task 4: `OscillatorPane` bottom `TimeAxis` + `set_bars` + `set_bottom_axis_visible`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `OscillatorPane.__init__` (lines 830–844) to add a bottom `TimeAxis`; add `set_bars`/`set_bottom_axis_visible` methods
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
def test_oscillator_pane_has_hidden_bottom_time_axis(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    assert isinstance(pane._time_axis, TimeAxis)
    assert pane.getAxis("bottom") is pane._time_axis
    assert pane.getAxis("bottom").isVisible() is False  # hidden at init (price chart owns it)


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
```

Also add `TimeAxis` to the import block at the top of the test file. Current import (lines 18–28):

```python
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
)
```

New import:

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_pane_has_hidden_bottom_time_axis tests/test_chart_indicators.py::test_oscillator_pane_set_bars_feeds_time_axis tests/test_chart_indicators.py::test_oscillator_pane_set_bottom_axis_visible_toggles -q
```
Expected FAIL: `AttributeError: 'OscillatorPane' object has no attribute '_time_axis'`.

- [ ] **Step 3: Minimal implementation**

(4a) Add the bottom axis at construction. Current `OscillatorPane.__init__` head (lines 830–831):

```python
    def __init__(self, link_to: "PriceChart"):
        super().__init__(axisItems={"right": PriceAxis(orientation="right")})
```

New:

```python
    def __init__(self, link_to: "PriceChart"):
        _time_axis = TimeAxis(orientation="bottom")
        super().__init__(axisItems={"right": PriceAxis(orientation="right"),
                                    "bottom": _time_axis})
        self._time_axis = _time_axis
```

(4b) Style + keep-hidden block. Current code (lines 843–849):

```python
        self.showAxis("right")
        self.hideAxis("left")
        self.hideAxis("bottom")  # time axis lives on the price chart; panes align via x-link
        self.getAxis("right").setTextPen(theme.TEXT3)
        _transparent = pg.mkPen(QtGui.QColor(0, 0, 0, 0))
        self.getAxis("right").setPen(_transparent)
        self.getAxis("right").setTickPen(pg.mkPen(theme.BORDER))
        self.getAxis("right").setStyle(tickLength=0)
```

New code (style the bottom axis like the price chart's; keep it hidden until reassigned to the lowest pane):

```python
        self.showAxis("right")
        self.hideAxis("left")
        # The bottom time axis is only SHOWN on the lowest pane (PriceChart._reassign_bottom_axis);
        # kept hidden here so non-lowest panes align via x-link without a duplicated axis strip.
        self.hideAxis("bottom")
        self.getAxis("right").setTextPen(theme.TEXT3)
        _transparent = pg.mkPen(QtGui.QColor(0, 0, 0, 0))
        self.getAxis("right").setPen(_transparent)
        self.getAxis("right").setTickPen(pg.mkPen(theme.BORDER))
        self.getAxis("right").setStyle(tickLength=0)
        # Bottom time axis styled exactly like the price chart's (transparent spine, BORDER
        # gridline pen, mono tick font) so the lowest pane's axis matches the rest of the chrome.
        _bottom = self.getAxis("bottom")
        _bottom.setTextPen(theme.TEXT3)
        _bottom.setPen(_transparent)
        _bottom.setTickPen(pg.mkPen(theme.BORDER))
        _tick_font = QtGui.QFont(theme.FONT_MONO.split(",")[0].strip('"'))
        _tick_font.setPixelSize(12)
        _bottom.setStyle(tickLength=0, tickFont=_tick_font)
```

(4c) Add the two methods at the end of `OscillatorPane`. Current tail (lines 943–946):

```python
    def refresh_legend(self):
        for ind in self._inds:
            if ind.uid in self._rows:
                self._rows[ind.uid].refresh(ind)
```

New (append the two methods after `refresh_legend`):

```python
    def refresh_legend(self):
        for ind in self._inds:
            if ind.uid in self._rows:
                self._rows[ind.uid].refresh(ind)

    def set_bars(self, bars):
        """Feed the pane's bottom time axis so its tick strings match the price chart's."""
        self._time_axis.set_bars(bars)

    def set_bottom_axis_visible(self, on: bool):
        """Show/hide this pane's bottom time axis (shown only on the lowest pane)."""
        self.showAxis("bottom") if on else self.hideAxis("bottom")
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_pane_has_hidden_bottom_time_axis tests/test_chart_indicators.py::test_oscillator_pane_set_bars_feeds_time_axis tests/test_chart_indicators.py::test_oscillator_pane_set_bottom_axis_visible_toggles -q
```
Expected PASS (3 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): give OscillatorPane a bottom TimeAxis + set_bars/set_bottom_axis_visible"
```

---

### Task 5: `_reassign_bottom_axis()` — bottom axis on the lowest pane only

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — add `_reassign_bottom_axis` on `PriceChart` (after `_sync_axis_width`)
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_reassign_bottom_axis_zero_panes_keeps_price_axis tests/test_chart_indicators.py::test_reassign_bottom_axis_moves_to_lowest_pane tests/test_chart_indicators.py::test_reassign_bottom_axis_follows_reorder tests/test_chart_indicators.py::test_reassign_bottom_axis_syncs_vb2 -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_reassign_bottom_axis'`.

- [ ] **Step 3: Minimal implementation**

Insert after `_sync_axis_width` (the method from Task 3), still ahead of `_osc_panes`. Anchor — the last line of `_sync_axis_width`:

```python
        finally:
            self._wsyncing = False
```

New code (insert after it):

```python
    def _reassign_bottom_axis(self):
        """Keep exactly one visible bottom time axis, on the LOWEST pane (TradingView puts the
        time scale under the lowest pane, not under the candles). With no panes the price chart
        keeps its own bottom axis."""
        panes = self._panes_in_visual_order()
        if not panes:
            self.showAxis("bottom")
            self._time_axis.set_bars(self._bars)
        else:
            self.hideAxis("bottom")
            for p in panes:
                p.set_bottom_axis_visible(False)
                p.set_bars(self._bars)
            panes[-1].set_bottom_axis_visible(True)  # lowest splitter index = bottom
        # hideAxis/showAxis only INVALIDATE the layout (lazy); force it + re-sync the own-scale
        # viewbox now so own-scale overlays don't lag behind the grown/shrunk price ViewBox.
        self.getPlotItem().layout.activate()
        self._sync_vb2()
        self._autorange_vb2()
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_reassign_bottom_axis_zero_panes_keeps_price_axis tests/test_chart_indicators.py::test_reassign_bottom_axis_moves_to_lowest_pane tests/test_chart_indicators.py::test_reassign_bottom_axis_follows_reorder tests/test_chart_indicators.py::test_reassign_bottom_axis_syncs_vb2 -q
```
Expected PASS (4 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _reassign_bottom_axis() puts the time axis on the lowest pane"
```

---

### Task 6: `_align_panes()` orchestrator

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — add `_align_panes` on `PriceChart` (after `_reassign_bottom_axis`)
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_align_panes_zero_panes_is_safe tests/test_chart_indicators.py::test_align_panes_reassigns_then_equalizes tests/test_chart_indicators.py::test_align_panes_order_reassign_before_sync -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_align_panes'`.

- [ ] **Step 3: Minimal implementation**

Insert after `_reassign_bottom_axis` (Task 5), still ahead of `_osc_panes`. Anchor — the last line of `_reassign_bottom_axis`:

```python
        self._sync_vb2()
        self._autorange_vb2()
```

New code (insert after it):

```python
    def _align_panes(self):
        """Re-align every pane in time after any layout/lifecycle change. Idempotent and safe
        with zero panes. Order matters: reassign the bottom axis FIRST (it changes which axes
        are visible and their natural widths), THEN equalize the right-axis width across the
        now-correct set of axes."""
        self._reassign_bottom_axis()
        self._sync_axis_width()
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_align_panes_zero_panes_is_safe tests/test_chart_indicators.py::test_align_panes_reassigns_then_equalizes tests/test_chart_indicators.py::test_align_panes_order_reassign_before_sync -q
```
Expected PASS (3 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add _align_panes() orchestrator (reassign-then-equalize)"
```

---

### Task 7: Wire `_align_panes` + `pane.set_bars` into every layout/lifecycle call site

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `_new_pane` (1331), `_unrender` pane-drop branch (1403–1409), `_reorder_pane` (1585), `_drag_pane` (both branches, 1343), `set_data` (1209), `apply_live` (1248), `set_timeframe` (1954), `resizeEvent` (before line 1938), and the tails of `move_indicator` (1583), `_merge_into_adjacent` (1609), `_render` oscillator branch (1387)
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_set_data_aligns_panes tests/test_chart_indicators.py::test_apply_live_feeds_pane_axes tests/test_chart_indicators.py::test_new_pane_seeds_bars_and_equalizes_on_add tests/test_chart_indicators.py::test_remove_last_pane_restores_price_axis tests/test_chart_indicators.py::test_reorder_keeps_bottom_axis_on_lowest tests/test_chart_indicators.py::test_drag_reorder_keeps_bottom_axis_on_lowest tests/test_chart_indicators.py::test_merge_keeps_bottom_axis_and_widths tests/test_chart_indicators.py::test_move_to_new_then_price_realigns tests/test_chart_indicators.py::test_set_timeframe_realigns -q
```
Expected FAIL: e.g. `test_new_pane_seeds_bars_and_equalizes_on_add` fails (`ind.pane._time_axis._bars` is empty / widths differ); `test_remove_last_pane_restores_price_axis` fails (`getAxis("bottom").isVisible()` stays False / `fixedWidth` not None); the reorder/merge/timeframe tests fail because no realign is wired yet.

- [ ] **Step 3: Minimal implementation**

(7a) `_new_pane` — seed bars + align. Current (lines 1331–1341):

```python
    def _new_pane(self) -> "OscillatorPane":
        pane = OscillatorPane(self)
        pane.editRequested.connect(self.edit_indicator)
        pane.removeRequested.connect(self.remove_indicator)
        pane.hideToggled.connect(self._toggle_visible)
        pane.moveRequested.connect(self.move_indicator)
        pane.actionRequested.connect(self._indicator_action)
        pane.dragMoved.connect(self._drag_pane)
        self._pane_host.addWidget(pane)
        self._resize_panes()
        return pane
```

New:

```python
    def _new_pane(self) -> "OscillatorPane":
        pane = OscillatorPane(self)
        pane.editRequested.connect(self.edit_indicator)
        pane.removeRequested.connect(self.remove_indicator)
        pane.hideToggled.connect(self._toggle_visible)
        pane.moveRequested.connect(self.move_indicator)
        pane.actionRequested.connect(self._indicator_action)
        pane.dragMoved.connect(self._drag_pane)
        self._pane_host.addWidget(pane)
        pane.set_bars(self._bars)  # so the fresh pane's time axis isn't blank
        self._resize_panes()
        self._align_panes()
        return pane
```

(7b) `_drag_pane` — align in both move branches. Current (lines 1351–1363):

```python
        if cur > 1:  # try to move above the upper neighbour
            up = host.widget(cur - 1)
            ctr = up.mapToGlobal(QtCore.QPoint(0, up.height() // 2)).y()
            if global_y < ctr:
                host.insertWidget(cur - 1, pane)
                self._resize_panes()
                return
        if cur < host.count() - 1:  # try to move below the lower neighbour
            down = host.widget(cur + 1)
            ctr = down.mapToGlobal(QtCore.QPoint(0, down.height() // 2)).y()
            if global_y > ctr:
                host.insertWidget(cur + 1, pane)
                self._resize_panes()
```

New:

```python
        if cur > 1:  # try to move above the upper neighbour
            up = host.widget(cur - 1)
            ctr = up.mapToGlobal(QtCore.QPoint(0, up.height() // 2)).y()
            if global_y < ctr:
                host.insertWidget(cur - 1, pane)
                self._resize_panes()
                self._align_panes()
                return
        if cur < host.count() - 1:  # try to move below the lower neighbour
            down = host.widget(cur + 1)
            ctr = down.mapToGlobal(QtCore.QPoint(0, down.height() // 2)).y()
            if global_y > ctr:
                host.insertWidget(cur + 1, pane)
                self._resize_panes()
                self._align_panes()
```

(7c) `_render` oscillator-branch tail. Current (lines 1382–1394):

```python
        elif ind.kind in ("oscillator", "pairs"):
            if self._pane_host is None:
                return
            if ind.pane is None:           # fresh add -> its own pane (merge sets ind.pane first)
                ind.pane = self._new_pane()
            ind.pane.add_ind(ind)
        elif ind.kind == "pattern":
            ind.scatter = pg.ScatterPlotItem(hoverable=True, pen=None,
                                             tip=lambda x, y, data: str(data))
            ind.scatter.setZValue(20)
            self.addItem(ind.scatter)
        self._reveal_indicator(ind, self._reveal_index())
        self._apply_visibility(ind)
```

New (re-align after the indicator lands in its pane — covers the merge path where `ind.pane` was set before `_render`, so `_new_pane`'s align didn't run):

```python
        elif ind.kind in ("oscillator", "pairs"):
            if self._pane_host is None:
                return
            if ind.pane is None:           # fresh add -> its own pane (merge sets ind.pane first)
                ind.pane = self._new_pane()
            ind.pane.add_ind(ind)
            self._align_panes()            # merge path: pane pre-set, so realign here too
        elif ind.kind == "pattern":
            ind.scatter = pg.ScatterPlotItem(hoverable=True, pen=None,
                                             tip=lambda x, y, data: str(data))
            ind.scatter.setZValue(20)
            self.addItem(ind.scatter)
        self._reveal_indicator(ind, self._reveal_index())
        self._apply_visibility(ind)
```

(7d) `_unrender` pane-drop branch — align after `setParent(None)`. Current (lines 1403–1409):

```python
        if ind.pane is not None:
            remaining = ind.pane.remove_ind(ind.uid)
            if remaining == 0:           # last indicator left the pane -> drop the pane
                ind.pane.setParent(None)
                ind.pane.deleteLater()
                self._resize_panes()
            ind.pane = None
```

New (realign immediately after detaching the pane from the splitter — do not wait for `deleteLater`):

```python
        if ind.pane is not None:
            remaining = ind.pane.remove_ind(ind.uid)
            if remaining == 0:           # last indicator left the pane -> drop the pane
                ind.pane.setParent(None)
                ind.pane.deleteLater()
                self._resize_panes()
                self._align_panes()      # after setParent(None): host no longer counts the pane
            ind.pane = None
```

(7e) `_apply_edit` — no change in Phase 1 (it is widened later in Phase 3, Task 67).

(7f) `_reorder_pane` tail. Current (lines 1585–1593):

```python
    def _reorder_pane(self, ind: "_Indicator", direction: str):
        host = self._pane_host
        if host is None or ind.pane is None:
            return
        idx = host.indexOf(ind.pane)
        new = idx - 1 if direction == "up" else idx + 1
        if 1 <= new <= host.count() - 1:   # keep below the price chart (index 0)
            host.insertWidget(new, ind.pane)
            self._resize_panes()
```

New:

```python
    def _reorder_pane(self, ind: "_Indicator", direction: str):
        host = self._pane_host
        if host is None or ind.pane is None:
            return
        idx = host.indexOf(ind.pane)
        new = idx - 1 if direction == "up" else idx + 1
        if 1 <= new <= host.count() - 1:   # keep below the price chart (index 0)
            host.insertWidget(new, ind.pane)
            self._resize_panes()
            self._align_panes()
```

(7g) `move_indicator` tail. Current (lines 1560–1583):

```python
    def move_indicator(self, uid: int, target: str):
        """Move an indicator between panes. ``target``: 'price' (overlay on the candles),
        'new' (its own oscillator pane), 'up'/'down' (reorder its pane), or
        'merge_above'/'merge_below' (merge into the adjacent pane)."""
        ind = self._indicators.get(uid)
        if ind is None:
            return
        if target == "price":
            if ind.kind != "overlay":
                self._unrender(ind)
                ind.kind = "overlay"
                ind.pane = None
                self._render(ind)
        elif target == "new":
            self._unrender(ind)
            ind.kind = "oscillator"
            ind.pane = None  # _render gives it a fresh pane
            self._render(ind)
        elif target in ("up", "down"):
            self._reorder_pane(ind, target)
            return
        elif target in ("merge_above", "merge_below"):
            self._merge_into_adjacent(ind, target)
        self._refresh_legends()
```

New (realign after the price/new/merge paths finalize; up/down already realign via `_reorder_pane` and early-return):

```python
    def move_indicator(self, uid: int, target: str):
        """Move an indicator between panes. ``target``: 'price' (overlay on the candles),
        'new' (its own oscillator pane), 'up'/'down' (reorder its pane), or
        'merge_above'/'merge_below' (merge into the adjacent pane)."""
        ind = self._indicators.get(uid)
        if ind is None:
            return
        if target == "price":
            if ind.kind != "overlay":
                self._unrender(ind)
                ind.kind = "overlay"
                ind.pane = None
                self._render(ind)
        elif target == "new":
            self._unrender(ind)
            ind.kind = "oscillator"
            ind.pane = None  # _render gives it a fresh pane
            self._render(ind)
        elif target in ("up", "down"):
            self._reorder_pane(ind, target)
            return
        elif target in ("merge_above", "merge_below"):
            self._merge_into_adjacent(ind, target)
        self._align_panes()  # finalize alignment after the unrender/render churn settles
        self._refresh_legends()
```

(7h) `_merge_into_adjacent` tail. Current (lines 1595–1609):

```python
    def _merge_into_adjacent(self, ind: "_Indicator", direction: str):
        host = self._pane_host
        if host is None or ind.pane is None:
            return
        idx = host.indexOf(ind.pane)
        tgt = idx - 1 if direction == "merge_above" else idx + 1
        if not (1 <= tgt <= host.count() - 1):
            return  # no adjacent oscillator pane (e.g. the price chart is above)
        target_pane = host.widget(tgt)
        if not isinstance(target_pane, OscillatorPane):
            return
        self._unrender(ind)                # detach from the current pane (drops it if now empty)
        ind.kind = "oscillator"
        ind.pane = target_pane             # _render adds it into the existing pane
        self._render(ind)
```

New:

```python
    def _merge_into_adjacent(self, ind: "_Indicator", direction: str):
        host = self._pane_host
        if host is None or ind.pane is None:
            return
        idx = host.indexOf(ind.pane)
        tgt = idx - 1 if direction == "merge_above" else idx + 1
        if not (1 <= tgt <= host.count() - 1):
            return  # no adjacent oscillator pane (e.g. the price chart is above)
        target_pane = host.widget(tgt)
        if not isinstance(target_pane, OscillatorPane):
            return
        self._unrender(ind)                # detach from the current pane (drops it if now empty)
        ind.kind = "oscillator"
        ind.pane = target_pane             # _render adds it into the existing pane
        self._render(ind)
        self._align_panes()                # pane count changed -> bottom axis must follow
```

(7i) `set_data` — feed pane axes + align. Current tail of `set_data` (lines 1208–1209):

```python
                self._conn.append((ei, t.entry_price, xi, t.exit_price))
        self.show_upto(len(bars) - 1)
```

New:

```python
                self._conn.append((ei, t.entry_price, xi, t.exit_price))
        for pane in self._osc_panes():  # re-feed each pane's time axis with the new bars
            pane.set_bars(bars)
        self.show_upto(len(bars) - 1)
        self._align_panes()
```

(7j) `apply_live` — feed pane axes + align. Current tail (lines 1247–1248):

```python
        if repaint:
            self.show_upto(len(bars) - 1)
```

New:

```python
        for pane in self._osc_panes():  # extend each pane's time axis to the live edge
            pane.set_bars(bars)
        if repaint:
            self.show_upto(len(bars) - 1)
        self._align_panes()
```

(7k) `set_timeframe` tail. Current (lines 1948–1957):

```python
    def set_timeframe(self, interval: str):
        """Update the timeframe selector label + current interval, and refresh per-interval
        indicator visibility (indicators restricted to other timeframes hide here)."""
        self._chart_interval = interval
        if hasattr(self, "_tf_btn"):
            self._tf_btn.setText(interval)
        if self._bars:
            for ind in self._indicators.values():
                self._sync_shown(ind)
                self._reveal_indicator(ind, self._reveal_index())
```

New:

```python
    def set_timeframe(self, interval: str):
        """Update the timeframe selector label + current interval, and refresh per-interval
        indicator visibility (indicators restricted to other timeframes hide here)."""
        self._chart_interval = interval
        if hasattr(self, "_tf_btn"):
            self._tf_btn.setText(interval)
        if self._bars:
            for ind in self._indicators.values():
                self._sync_shown(ind)
                self._reveal_indicator(ind, self._reveal_index())
        self._align_panes()
```

(7l) `resizeEvent` — realign before the right-axis width read. Current (lines 1933–1939):

```python
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if hasattr(self, "_top_bar"):
            # span the chart width but stop short of the right price-axis labels, so the
            # far-right range selector clears them.
            axis_w = self.getAxis("right").width() if self.getAxis("right").isVisible() else 0
            self._top_bar.setGeometry(0, 4, max(0, self.width() - int(axis_w) - 6), 28)
```

New (re-equalize before reading the shared axis width for the top-bar geometry; guard for early resizes during `super().__init__`):

```python
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if getattr(self, "_pane_host", None) is not None and self._panes_in_visual_order():
            self._align_panes()  # width settles after a resize -> re-equalize before reading it
        if hasattr(self, "_top_bar"):
            # span the chart width but stop short of the right price-axis labels, so the
            # far-right range selector clears them.
            axis_w = self.getAxis("right").width() if self.getAxis("right").isVisible() else 0
            self._top_bar.setGeometry(0, 4, max(0, self.width() - int(axis_w) - 6), 28)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_set_data_aligns_panes tests/test_chart_indicators.py::test_apply_live_feeds_pane_axes tests/test_chart_indicators.py::test_new_pane_seeds_bars_and_equalizes_on_add tests/test_chart_indicators.py::test_remove_last_pane_restores_price_axis tests/test_chart_indicators.py::test_reorder_keeps_bottom_axis_on_lowest tests/test_chart_indicators.py::test_drag_reorder_keeps_bottom_axis_on_lowest tests/test_chart_indicators.py::test_merge_keeps_bottom_axis_and_widths tests/test_chart_indicators.py::test_move_to_new_then_price_realigns tests/test_chart_indicators.py::test_set_timeframe_realigns -q
```
Expected PASS (9 passed).

- [ ] **Step 5: Run the whole file (regression — baseline + new must stay green)**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected PASS (all green — the 37 baseline tests plus the Phase 1 additions).

- [ ] **Step 6: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): wire _align_panes + pane.set_bars into all layout/lifecycle paths"
```

---

### Task 8: `OscillatorPane` min-height + lowest-pane axis-strip allotment in `_resize_panes`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `OscillatorPane.__init__` (add `setMinimumHeight`); `_resize_panes` (1691–1700)
- Test: `tests/test_chart_indicators.py` (append)

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_pane_has_min_height tests/test_chart_indicators.py::test_resize_panes_gives_lowest_pane_axis_strip -q
```
Expected FAIL: `test_oscillator_pane_has_min_height` fails (`minimumHeight()` is 0); `test_resize_panes_gives_lowest_pane_axis_strip` fails (all panes get equal height — the lowest isn't given the extra strip).

- [ ] **Step 3: Minimal implementation**

(8a) Min-height in `OscillatorPane.__init__`. Add right after the `self._time_axis = _time_axis` / hidden-axis setup. Anchor — the bottom-axis styling block added in Task 4 ends with:

```python
        _bottom.setStyle(tickLength=0, tickFont=_tick_font)
```

New (append after it, before `self.showGrid(...)` at original line 850):

```python
        _bottom.setStyle(tickLength=0, tickFont=_tick_font)
        # Splitter floor so a pane never collapses below a readable height, independent of
        # _resize_panes (Phase 2 disables that while a pane is maximized).
        self.setMinimumHeight(64)
```

(8b) Axis-strip allotment in `_resize_panes`. Current (lines 1691–1700):

```python
    def _resize_panes(self):
        """Give the price chart the bulk of the height; each oscillator pane ~22% (stacked)."""
        host = self._pane_host
        if host is None or host.count() <= 1:
            return
        n_panes = host.count() - 1
        total = host.height() or 600
        pane_h = max(96, int(total * 0.22))
        price_h = max(140, total - pane_h * n_panes)
        host.setSizes([price_h] + [pane_h] * n_panes)
```

New (give the lowest pane an extra ~20px for the bottom-axis strip so plot areas match):

```python
    def _resize_panes(self):
        """Give the price chart the bulk of the height; each oscillator pane ~22% (stacked).
        The LOWEST pane gets an extra axis-strip (~20px) so its PLOT area matches its siblings'
        (the bottom time axis lives there); cosmetic only — x-alignment is independent."""
        host = self._pane_host
        if host is None or host.count() <= 1:
            return
        n_panes = host.count() - 1
        total = host.height() or 600
        axis_strip = 20  # bottom time-axis height on the lowest pane
        pane_h = max(96, int(total * 0.22))
        price_h = max(140, total - pane_h * n_panes - axis_strip)
        sizes = [price_h] + [pane_h] * n_panes
        sizes[-1] += axis_strip  # lowest pane carries the axis strip
        host.setSizes(sizes)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_pane_has_min_height tests/test_chart_indicators.py::test_resize_panes_gives_lowest_pane_axis_strip -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): OscillatorPane min-height + lowest-pane axis-strip allotment"
```

---

### Task 9: Hide the orphaned `_cx_time_tag` when panes exist

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `_on_mouse_moved` (1924–1929)
- Test: `tests/test_chart_indicators.py` (append)

Rationale (spec 3d / risk register): once the time axis moves to the lowest pane, the price-chart crosshair time tag (`_cx_time_tag`, drawn at `height()-tag_height`) is orphaned with no axis beneath it. Phase-1 fix: hide it when panes exist. The full cross-pane crosshair + bottom-pane time tag is Phase 4.

Steps:

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_crosshair_time_tag_hidden_when_panes_exist tests/test_chart_indicators.py::test_crosshair_time_tag_shown_with_no_panes -q
```
Expected FAIL: `test_crosshair_time_tag_hidden_when_panes_exist` fails — `_cx_time_tag` is shown unconditionally.

- [ ] **Step 3: Minimal implementation**

Current `_on_mouse_moved` time-tag block (lines 1924–1929):

```python
        dt = datetime.fromtimestamp(x_to_ts(self._bars, pt.x()) / 1000, tz=timezone.utc)
        self._cx_time_tag.setText(dt.strftime("%m-%d %H:%M"))
        self._cx_time_tag.adjustSize()
        self._cx_time_tag.move(int(scene_pos.x()) - self._cx_time_tag.width() // 2,
                               self.height() - self._cx_time_tag.height() - 1)
        self._cx_time_tag.show()
```

New (when panes exist the bottom axis lives on the lowest pane, so the price-chart time tag is orphaned — hide it; full cross-pane time tag is Phase 4):

```python
        if self._panes_in_visual_order():
            # the time axis moved to the lowest pane -> this tag would float over the price plot
            # with no axis beneath it. Hide it (Phase 4 adds the bottom-pane time tag).
            self._cx_time_tag.hide()
        else:
            dt = datetime.fromtimestamp(x_to_ts(self._bars, pt.x()) / 1000, tz=timezone.utc)
            self._cx_time_tag.setText(dt.strftime("%m-%d %H:%M"))
            self._cx_time_tag.adjustSize()
            self._cx_time_tag.move(int(scene_pos.x()) - self._cx_time_tag.width() // 2,
                                   self.height() - self._cx_time_tag.height() - 1)
            self._cx_time_tag.show()
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_crosshair_time_tag_hidden_when_panes_exist tests/test_chart_indicators.py::test_crosshair_time_tag_shown_with_no_panes -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "fix(chart): hide orphaned crosshair time tag when oscillator panes exist"
```

---

### Task 10: Studio-instance parity + full-file regression

**Files**
- Test: `tests/test_chart_indicators.py` (append)
- No source change (parity falls out of per-instance state established in Tasks 1–9).

Rationale (spec 3e / risk register): `app.py` mounts a second `PriceChart` (`self.studio_price`). All Phase-1 state (`_wsyncing`, `_pane_host`, axis pinning) is per-instance, so a second chart must align independently with no cross-talk.

Steps:

- [ ] **Step 1: Write failing/regression test**

```python
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
```

- [ ] **Step 2: Run-to-fail / verify**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_studio_second_chart_aligns_independently -q
```
Expected: PASS (per-instance state from Tasks 1–9 already delivers parity). If it FAILS, the failure exposes leaked module/class state and must be fixed before proceeding.

- [ ] **Step 3: Full-file regression**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected PASS: every test green — the 37 baseline tests plus all Phase 1 additions.

- [ ] **Step 4: Commit**

```
git add tests/test_chart_indicators.py
git commit -m "test(chart): studio-instance time-alignment parity + Phase 1 regression"
```

---

## Phase 2 — Pane hover toolbar (move up/down, maximize, delete)

### Task 30: `_pane_icon(kind)` painter glyphs

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` (add module fn after `_eye_icon`, at `chart.py:568`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
def test_pane_icon_renders_all_kinds(app):
    from vike_trader_app.ui.chart import _pane_icon
    for kind in ("up", "down", "max", "restore", "del"):
        ic = _pane_icon(kind)
        assert isinstance(ic, QtGui.QIcon)
        assert not ic.isNull()
        pm = ic.pixmap(18, 18)
        assert not pm.isNull() and pm.width() > 0
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_icon_renders_all_kinds -q
```
Expected FAIL: `ImportError: cannot import name '_pane_icon' from vike_trader_app.ui.chart`.

- [ ] **Step 3: Minimal implementation**
Add a new module-level function immediately after `_eye_icon` (which ends at `chart.py:568` with `return QtGui.QIcon(pm)`). Insert after that line and its trailing blank line:
```python
def _pane_icon(kind: str) -> QtGui.QIcon:
    """Painter-drawn glyph for the pane hover toolbar — `up`/`down`/`max`/`restore`/`del`
    (theme.TEXT3, no image assets), mirroring `_eye_icon`'s pixmap recipe."""
    s, dpr = 18, 2
    pm = QtGui.QPixmap(s * dpr, s * dpr)
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor(theme.TEXT3))
    pen.setWidthF(1.5)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.setPen(pen)
    if kind in ("up", "down"):
        # chevron arrow
        if kind == "up":
            p.drawLine(QtCore.QPointF(4, 11), QtCore.QPointF(9, 6))
            p.drawLine(QtCore.QPointF(9, 6), QtCore.QPointF(14, 11))
        else:
            p.drawLine(QtCore.QPointF(4, 7), QtCore.QPointF(9, 12))
            p.drawLine(QtCore.QPointF(9, 12), QtCore.QPointF(14, 7))
    elif kind == "max":
        p.drawRect(QtCore.QRectF(4, 4, 10, 10))          # outer frame = maximize
    elif kind == "restore":
        p.drawRect(QtCore.QRectF(4, 6, 8, 8))            # two offset frames = restore
        p.drawLine(QtCore.QPointF(6, 6), QtCore.QPointF(6, 4))
        p.drawLine(QtCore.QPointF(6, 4), QtCore.QPointF(14, 4))
        p.drawLine(QtCore.QPointF(14, 4), QtCore.QPointF(14, 12))
        p.drawLine(QtCore.QPointF(14, 12), QtCore.QPointF(12, 12))
    elif kind == "del":
        p.drawLine(QtCore.QPointF(4, 5), QtCore.QPointF(14, 5))   # trash lid
        p.drawLine(QtCore.QPointF(7, 5), QtCore.QPointF(7, 3))
        p.drawLine(QtCore.QPointF(7, 3), QtCore.QPointF(11, 3))
        p.drawLine(QtCore.QPointF(11, 3), QtCore.QPointF(11, 5))
        path = QtGui.QPainterPath()                              # trash body
        path.moveTo(5, 6)
        path.lineTo(6, 15)
        path.lineTo(12, 15)
        path.lineTo(13, 6)
        p.drawPath(path)
    p.end()
    return QtGui.QIcon(pm)
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_icon_renders_all_kinds -q
```
Expected PASS (1 passed).

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _pane_icon painter glyphs for pane hover toolbar

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 31: `_PaneToolbar(QWidget)` — 4 buttons, signals, state setters

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` (add class after `_pane_icon`, before `class _DragGrip` at `chart.py:571`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_toolbar_signals_and_state -q
```
Expected FAIL: `ImportError: cannot import name '_PaneToolbar' from vike_trader_app.ui.chart`.

- [ ] **Step 3: Minimal implementation**
Insert this class directly after the `_pane_icon` function added in Task 30, and immediately before `class _DragGrip(QtWidgets.QLabel):` (currently `chart.py:571`):
```python
class _PaneToolbar(QtWidgets.QWidget):
    """A small floating horizontal strip of 4 buttons (move up / move down / maximize-restore /
    delete pane), shown on pane hover at the top-right — TradingView's per-pane toolbar. Styled
    like `_LegendRow._btn` (transparent, autoRaise, TEXT3 -> TEXT on hover). Parented to the pane
    as a child overlay (like `_header`); hidden by default."""

    moveUp = QtCore.Signal()
    moveDown = QtCore.Signal()
    maximizeToggled = QtCore.Signal()
    deletePane = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background:transparent;")
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        self._up = self._btn(_pane_icon("up"), "Move pane up")
        self._down = self._btn(_pane_icon("down"), "Move pane down")
        self._max = self._btn(_pane_icon("max"), "Maximize pane")
        self._del = self._btn(_pane_icon("del"), "Delete pane")
        self._up.clicked.connect(self.moveUp)
        self._down.clicked.connect(self.moveDown)
        self._max.clicked.connect(self.maximizeToggled)
        self._del.clicked.connect(self.deletePane)
        for b in (self._up, self._down, self._max, self._del):
            h.addWidget(b)
        self.adjustSize()

    def _btn(self, icon: QtGui.QIcon, tip: str) -> QtWidgets.QToolButton:
        b = QtWidgets.QToolButton(self)
        b.setCursor(QtCore.Qt.PointingHandCursor)
        b.setAutoRaise(True)
        b.setIcon(icon)
        b.setIconSize(QtCore.QSize(15, 15))
        b.setToolTip(tip)
        b.setStyleSheet(
            f"QToolButton{{background:transparent;border:none;color:{theme.TEXT3};padding:0 2px;}}"
            f"QToolButton:hover{{color:{theme.TEXT};}}"
        )
        return b

    def set_can_up(self, on: bool):
        self._up.setEnabled(on)

    def set_can_down(self, on: bool):
        self._down.setEnabled(on)

    def set_maximized(self, on: bool):
        self._max.setIcon(_pane_icon("restore" if on else "max"))
        self._max.setToolTip("Restore pane" if on else "Maximize pane")
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_toolbar_signals_and_state -q
```
Expected PASS (1 passed).

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _PaneToolbar widget with move/max/delete buttons + state setters

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 32: `OscillatorPane` pane-level signals + toolbar instance + positioning + hover

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `OscillatorPane` signals (`chart.py:822-828`), `OscillatorPane.__init__` (`chart.py:830-870`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 120)
    pane._toolbar.hide()
    pane.enterEvent(None)
    assert pane._toolbar.isVisible()
    pane.leaveEvent(None)
    assert not pane._toolbar.isVisible()
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_oscillator_pane_has_toolbar_and_signals" "tests/test_chart_indicators.py::test_oscillator_pane_hover_shows_hides_toolbar" -q
```
Expected FAIL: `AttributeError: 'OscillatorPane' object has no attribute '_toolbar'` (and no `paneMoveUp`).

- [ ] **Step 3: Minimal implementation**

**3a.** Add the 4 pane-level signals. Current code (`chart.py:826-828`):
```python
    actionRequested = QtCore.Signal(int, str)
    dragMoved = QtCore.Signal(object, int)  # (pane, cursor global y) — drag-to-reorder
    dragEnded = QtCore.Signal()
```
Replace with:
```python
    actionRequested = QtCore.Signal(int, str)
    dragMoved = QtCore.Signal(object, int)  # (pane, cursor global y) — drag-to-reorder
    dragEnded = QtCore.Signal()
    # pane-level (carry the pane, so a multi-indicator merged pane moves/deletes atomically)
    paneMoveUp = QtCore.Signal(object)
    paneMoveDown = QtCore.Signal(object)
    paneMaximizeToggled = QtCore.Signal(object)
    paneDeleteRequested = QtCore.Signal(object)
```

**3b.** Instantiate the toolbar + a hover re-check timer at the end of `__init__`. Current tail of `OscillatorPane.__init__` (`chart.py:869-870`):
```python
        _hh.addWidget(_rowscol)
        self._header.move(6, 3)
```
Replace with:
```python
        _hh.addWidget(_rowscol)
        self._header.move(6, 3)

        # TradingView-style per-pane hover toolbar (move up/down / maximize / delete), top-right.
        self._toolbar = _PaneToolbar(self)
        self._toolbar.moveUp.connect(lambda: self.paneMoveUp.emit(self))
        self._toolbar.moveDown.connect(lambda: self.paneMoveDown.emit(self))
        self._toolbar.maximizeToggled.connect(lambda: self.paneMaximizeToggled.emit(self))
        self._toolbar.deletePane.connect(lambda: self.paneDeleteRequested.emit(self))
        self._toolbar.hide()
        # belt-and-braces: re-check cursor-in-rect before hiding so the bar survives a menu/popup.
        self._tb_timer = QtCore.QTimer(self)
        self._tb_timer.setInterval(120)
        self._tb_timer.setSingleShot(True)
        self._tb_timer.timeout.connect(self._maybe_hide_toolbar)
```

**3c.** Add positioning + hover methods. Insert immediately after `OscillatorPane.refresh_legend` (ends `chart.py:946` with the row refresh loop), i.e. just before `class PriceChart` at `chart.py:949`:
```python
    def _position_toolbar(self):
        """Tuck the hover toolbar at the top-right, just left of the (shared-width) price axis."""
        tb = getattr(self, "_toolbar", None)
        if tb is None:
            return
        tb.adjustSize()
        axis_w = int(self.getAxis("right").width()) if self.getAxis("right").isVisible() else 0
        x = self.width() - axis_w - tb.width() - 4
        tb.move(max(0, x), 3)
        tb.raise_()

    def _cursor_in_rect(self) -> bool:
        return self.rect().contains(self.mapFromGlobal(QtGui.QCursor.pos()))

    def _maybe_hide_toolbar(self):
        if not self._cursor_in_rect():
            self._toolbar.hide()

    def enterEvent(self, e):  # noqa: N802 - Qt override
        self._position_toolbar()
        self._toolbar.show()
        self._toolbar.raise_()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802 - Qt override
        # child-button hover doesn't fire a parent leave in Qt (no flicker); re-check on a delay
        # so opening a menu/popup over the bar doesn't dismiss it.
        self._tb_timer.start()
        super().leaveEvent(e)

    def resizeEvent(self, e):  # noqa: N802 - Qt override
        super().resizeEvent(e)
        self._position_toolbar()
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_oscillator_pane_has_toolbar_and_signals" "tests/test_chart_indicators.py::test_oscillator_pane_hover_shows_hides_toolbar" -q
```
Expected PASS (2 passed). Note: `leaveEvent` starts the 120 ms timer; the test asserts immediate hide because `_cursor_in_rect()` is false offscreen — but the timer fires later. The test calls `leaveEvent` then asserts hidden, so the hide must be immediate when the cursor is already out. Adjust `leaveEvent` to hide immediately when cursor is out, else arm the timer:
```python
    def leaveEvent(self, e):  # noqa: N802 - Qt override
        if self._cursor_in_rect():
            self._tb_timer.start()   # cursor moved onto a child/popup: re-check shortly
        else:
            self._toolbar.hide()
        super().leaveEvent(e)
```
(Apply this corrected `leaveEvent` in Step 3c instead of the first draft, then run-to-pass.)

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): OscillatorPane hover toolbar — signals, positioning, enter/leave

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 33: `PriceChart.__init__` maximize state + `_new_pane` wiring + `_refresh_pane_toolbars`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `PriceChart.__init__` (`chart.py:998` area), `_new_pane` (`chart.py:1331-1341`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_new_pane_wires_toolbar_and_refreshes -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_maximized_pane'`.

- [ ] **Step 3: Minimal implementation**

**3a.** Init the maximize state. Current code (`chart.py:998`):
```python
        self._vb2 = None  # secondary ViewBox for overlays pinned to their own scale
```
Replace with:
```python
        self._vb2 = None  # secondary ViewBox for overlays pinned to their own scale
        self._wsyncing = False     # Phase 1: guard against axis-width sync re-entrancy
        self._maximized_pane = None  # the pane currently maximized (locks _resize_panes)
        self._saved_sizes = None     # host.sizes() snapshot to restore on un-maximize
```
*(Note: `self._wsyncing = False` is the Phase-1 field; include it here only if Phase 1 has not already added it. If Phase 1 already initializes `_wsyncing` at this line, drop that line and add only the two maximize fields.)*

**3b.** Wire the 4 pane signals + refresh in `_new_pane`. Current code (`chart.py:1331-1341`):
```python
    def _new_pane(self) -> "OscillatorPane":
        pane = OscillatorPane(self)
        pane.editRequested.connect(self.edit_indicator)
        pane.removeRequested.connect(self.remove_indicator)
        pane.hideToggled.connect(self._toggle_visible)
        pane.moveRequested.connect(self.move_indicator)
        pane.actionRequested.connect(self._indicator_action)
        pane.dragMoved.connect(self._drag_pane)
        self._pane_host.addWidget(pane)
        self._resize_panes()
        return pane
```
Replace with:
```python
    def _new_pane(self) -> "OscillatorPane":
        pane = OscillatorPane(self)
        pane.editRequested.connect(self.edit_indicator)
        pane.removeRequested.connect(self.remove_indicator)
        pane.hideToggled.connect(self._toggle_visible)
        pane.moveRequested.connect(self.move_indicator)
        pane.actionRequested.connect(self._indicator_action)
        pane.dragMoved.connect(self._drag_pane)
        pane.paneMoveUp.connect(self._pane_move_up)
        pane.paneMoveDown.connect(self._pane_move_down)
        pane.paneMaximizeToggled.connect(self._toggle_maximize_pane)
        pane.paneDeleteRequested.connect(self._delete_pane)
        self._pane_host.addWidget(pane)
        self._resize_panes()
        self._refresh_pane_toolbars()
        return pane
```

**3c.** Add `_refresh_pane_toolbars`. Insert immediately after `_resize_panes` (ends `chart.py:1700`, the `host.setSizes(...)` line) and before `_refresh_legends` at `chart.py:1702`:
```python
    def _refresh_pane_toolbars(self):
        """Sync every pane's hover-toolbar state to its current visual position: up enabled when
        a pane is above, down enabled when a pane is below, max glyph reflecting the maximized
        pane. Also re-tucks each toolbar left of the (now-settled) shared right axis."""
        panes = self._panes_in_visual_order()
        n = len(panes)
        for p, pane in enumerate(panes):
            tb = getattr(pane, "_toolbar", None)
            if tb is None:
                continue
            tb.set_can_up(p > 0)
            tb.set_can_down(p < n - 1)
            tb.set_maximized(pane is self._maximized_pane)
            pane._position_toolbar()
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_new_pane_wires_toolbar_and_refreshes -q
```
Expected PASS (1 passed). (Each `add_indicator` -> `_render` -> `_new_pane` -> `_refresh_pane_toolbars`; the second add refreshes both panes.)

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): wire pane toolbar signals in _new_pane + _refresh_pane_toolbars

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 34: `_pane_move_up` / `_pane_move_down` / `_after_pane_reorder`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — add methods after `_reorder_pane` (`chart.py:1593`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_pane_move_up_down_reorders_splitter" "tests/test_chart_indicators.py::test_pane_move_clamps_at_edges" "tests/test_chart_indicators.py::test_after_pane_reorder_realigns" -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_pane_move_up'`.

- [ ] **Step 3: Minimal implementation**
Insert after `_reorder_pane` (which ends `chart.py:1593` with `self._resize_panes()`) and before `_merge_into_adjacent` at `chart.py:1595`:
```python
    def _pane_move_up(self, pane):
        """Move a whole pane up one slot via its hover toolbar (keyed off the pane object, so a
        merged multi-indicator pane moves atomically). Clamped to index >= 1 (never above price)."""
        host = self._pane_host
        if host is None:
            return
        idx = host.indexOf(pane)
        if idx <= 1:
            return  # already topmost oscillator pane (price is fixed at index 0)
        host.insertWidget(idx - 1, pane)
        self._after_pane_reorder()

    def _pane_move_down(self, pane):
        host = self._pane_host
        if host is None:
            return
        idx = host.indexOf(pane)
        if idx < 1 or idx >= host.count() - 1:
            return  # already the bottom pane
        host.insertWidget(idx + 1, pane)
        self._after_pane_reorder()

    def _after_pane_reorder(self):
        """Common tail for a toolbar-driven reorder: resize, re-tag toolbars, and realign the
        shared axis + bottom-time axis to the new lowest pane (Phase 1)."""
        self._resize_panes()
        self._refresh_pane_toolbars()
        self._align_panes()
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_pane_move_up_down_reorders_splitter" "tests/test_chart_indicators.py::test_pane_move_clamps_at_edges" "tests/test_chart_indicators.py::test_after_pane_reorder_realigns" -q
```
Expected PASS (3 passed).

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _pane_move_up/_pane_move_down + _after_pane_reorder (align panes)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 35: `_delete_pane` — remove all indicators in a (merged) pane

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — add `_delete_pane` after `_after_pane_reorder` (`chart.py:1595` region, post-Task 34)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_delete_pane_drops_single_indicator" "tests/test_chart_indicators.py::test_delete_merged_pane_removes_all_indicators" -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_delete_pane'`.

- [ ] **Step 3: Minimal implementation**
Insert directly after `_after_pane_reorder` (added in Task 34, ends with `self._align_panes()`) and before `_merge_into_adjacent` at `chart.py:1595`:
```python
    def _delete_pane(self, pane):
        """Delete a whole pane via its toolbar: remove every indicator it hosts (the last
        removal triggers `_unrender`'s empty-pane teardown), then re-tag the survivors. Null any
        dangling maximized-pane lock so we don't reference a deleted QWidget."""
        if pane is self._maximized_pane:
            self._maximized_pane = None
        for uid in list(pane.uids):
            self.remove_indicator(uid)
        self._refresh_pane_toolbars()
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_delete_pane_drops_single_indicator" "tests/test_chart_indicators.py::test_delete_merged_pane_removes_all_indicators" -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _delete_pane removes all indicators in a merged pane

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 36: `_resize_panes` early-return while maximized + null lock in `_unrender` pane-drop

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `_resize_panes` (`chart.py:1691-1700`), `_unrender` pane-drop branch (`chart.py:1403-1409`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
def test_resize_panes_noop_while_maximized(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_resize_panes_noop_while_maximized" "tests/test_chart_indicators.py::test_unrender_pane_drop_clears_maximize_lock" -q
```
Expected FAIL: `_resize_panes` overwrites the sentinel sizes (no early-return); and `_maximized_pane` stays set after the pane is dropped.

- [ ] **Step 3: Minimal implementation**

**3a.** Early-return `_resize_panes` while maximized. Current code (`chart.py:1691-1700`):
```python
    def _resize_panes(self):
        """Give the price chart the bulk of the height; each oscillator pane ~22% (stacked)."""
        host = self._pane_host
        if host is None or host.count() <= 1:
            return
        n_panes = host.count() - 1
        total = host.height() or 600
        pane_h = max(96, int(total * 0.22))
        price_h = max(140, total - pane_h * n_panes)
        host.setSizes([price_h] + [pane_h] * n_panes)
```
Replace with:
```python
    def _resize_panes(self):
        """Give the price chart the bulk of the height; each oscillator pane ~22% (stacked).
        No-op while a pane is maximized so add/remove/reorder don't stomp the maximized layout."""
        host = self._pane_host
        if host is None or host.count() <= 1:
            return
        if self._maximized_pane is not None:
            return
        n_panes = host.count() - 1
        total = host.height() or 600
        pane_h = max(96, int(total * 0.22))
        price_h = max(140, total - pane_h * n_panes)
        host.setSizes([price_h] + [pane_h] * n_panes)
```

**3b.** Null the lock in `_unrender`'s pane-drop branch. Current code (`chart.py:1403-1409`):
```python
        if ind.pane is not None:
            remaining = ind.pane.remove_ind(ind.uid)
            if remaining == 0:           # last indicator left the pane -> drop the pane
                ind.pane.setParent(None)
                ind.pane.deleteLater()
                self._resize_panes()
            ind.pane = None
```
Replace with:
```python
        if ind.pane is not None:
            remaining = ind.pane.remove_ind(ind.uid)
            if remaining == 0:           # last indicator left the pane -> drop the pane
                if ind.pane is self._maximized_pane:
                    self._maximized_pane = None  # avoid a dangling deleted-QWidget ref
                ind.pane.setParent(None)
                ind.pane.deleteLater()
                self._resize_panes()
            ind.pane = None
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_resize_panes_noop_while_maximized" "tests/test_chart_indicators.py::test_unrender_pane_drop_clears_maximize_lock" -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _resize_panes no-op while maximized + clear lock on pane drop

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 37: `_toggle_maximize_pane` — maximize with price floor + restore preserving user sizes

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — add `_toggle_maximize_pane` after `_delete_pane` (`chart.py:1595` region, post-Task 35)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
    b = pc.add_indicator("macd")
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
    b = pc.add_indicator("macd")
    pc._toggle_maximize_pane(a.pane)
    assert pc._maximized_pane is a.pane
    pc._delete_pane(a.pane)
    assert pc._maximized_pane is None            # no dangling deleted-QWidget ref
    assert a.uid not in pc._indicators and split.count() == 2
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_maximize_gives_dominant_share_with_price_floor" "tests/test_chart_indicators.py::test_restore_preserves_user_dragged_sizes" "tests/test_chart_indicators.py::test_delete_maximized_pane_clears_lock" -q
```
Expected FAIL: `AttributeError: 'PriceChart' object has no attribute '_toggle_maximize_pane'`.

- [ ] **Step 3: Minimal implementation**
Insert directly after `_delete_pane` (added Task 35) and before `_merge_into_adjacent` at `chart.py:1595`:
```python
    def _toggle_maximize_pane(self, pane):
        """Toggle a pane between maximized and the normal stacked layout (TradingView's pane
        maximize). Maximizing keeps a real price floor so OHLC stays visible; restoring replays
        the user's pre-maximize splitter proportions when the pane count is unchanged."""
        host = self._pane_host
        if host is None:
            return
        if pane is self._maximized_pane:        # --- restore ---
            self._maximized_pane = None
            if self._saved_sizes is not None and len(self._saved_sizes) == host.count():
                host.setSizes(self._saved_sizes)  # preserve user-dragged proportions (TV)
            else:
                self._resize_panes()
            self._saved_sizes = None
            pane.set_maximized(False)
        else:                                    # --- maximize ---
            self._saved_sizes = host.sizes()
            self._maximized_pane = pane
            total = sum(self._saved_sizes) or (host.height() or 600)
            n = host.count()
            idx = host.indexOf(pane)
            price_floor = max(140, int(total * 0.15))
            others = max(1, n - 2)               # panes that aren't price and aren't maximized
            slim = 1                             # minimal share for the non-maximized panes
            big = max(price_floor, total - price_floor - slim * others)
            sizes = [slim] * n
            sizes[0] = price_floor
            sizes[idx] = big
            host.setSizes(sizes)
            pane.set_maximized(True)
        self._refresh_pane_toolbars()
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_maximize_gives_dominant_share_with_price_floor" "tests/test_chart_indicators.py::test_restore_preserves_user_dragged_sizes" "tests/test_chart_indicators.py::test_delete_maximized_pane_clears_lock" -q
```
Expected PASS (3 passed). `_delete_pane` (Task 35) already nulls `_maximized_pane` before removing indicators, so the third test passes.

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _toggle_maximize_pane with price floor + restore user sizes

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 38: `set_pane_host` wires `splitterMoved` → clear maximize + refresh toolbars

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — `set_pane_host` (`chart.py:1263-1265`)
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
def test_splitter_drag_clears_maximize_lock(app):
    pc, split = _chart(app)
    a = pc.add_indicator("rsi")
    b = pc.add_indicator("macd")
    pc._toggle_maximize_pane(a.pane)
    assert pc._maximized_pane is a.pane
    split.splitterMoved.emit(0, 1)              # a manual drag of a handle
    assert pc._maximized_pane is None            # exits maximize, like TV
    assert a.pane._toolbar._max.toolTip() == "Maximize pane"
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_splitter_drag_clears_maximize_lock -q
```
Expected FAIL: `assert pc._maximized_pane is None` fails (lock stays set; `splitterMoved` is not yet connected), and the max tooltip still reads "Restore pane".

- [ ] **Step 3: Minimal implementation**

**3a.** Add the handler. Insert right before `set_pane_host` at `chart.py:1263`:
```python
    def _on_splitter_moved(self, *_):
        """A manual splitter drag exits maximize (like TV) and re-tags the pane toolbars."""
        if self._maximized_pane is not None:
            pane = self._maximized_pane
            self._maximized_pane = None
            self._saved_sizes = None
            if pane is not None:
                pane.set_maximized(False)
        self._refresh_pane_toolbars()
```

**3b.** Connect it in `set_pane_host`. Current code (`chart.py:1263-1265`):
```python
    def set_pane_host(self, splitter):
        """Give the chart the vertical QSplitter it shares with its oscillator sub-panes."""
        self._pane_host = splitter
```
Replace with:
```python
    def set_pane_host(self, splitter):
        """Give the chart the vertical QSplitter it shares with its oscillator sub-panes."""
        self._pane_host = splitter
        splitter.splitterMoved.connect(self._on_splitter_moved)
```

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_splitter_drag_clears_maximize_lock -q
```
Expected PASS (1 passed). `set_maximized(False)` is also re-applied in `_refresh_pane_toolbars` (which now sets `maximized=pane is None==False` for every pane), so the tooltip flips back.

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): splitterMoved clears maximize lock + refreshes pane toolbars

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 39: Toolbar clears the right axis after layout settles

**Files**
- Modify: `src/vike_trader_app/ui/chart.py` — call `_refresh_pane_toolbars()` from `_align_panes` (Phase 1, `chart.py` `_align_panes` body) **only if not already invoked there**; otherwise rely on `_after_pane_reorder` and `_new_pane`. This task adds a `_position_toolbar` call after `show_upto` settles axis width.
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**
```python
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_toolbar_clears_right_axis_after_layout -q
```
Expected: this likely PASSES already if `_refresh_pane_toolbars` -> `_position_toolbar` ran after axis width settled. If it FAILS (toolbar overlaps the axis because width wasn't re-read post-`show_upto`), the fix below makes it deterministic. Run it; if green, the assertion already holds via Task 33's `_position_toolbar` in `_refresh_pane_toolbars`. If red, expected reason: `tb.x()+tb.width() > pane.width()-axis_w` because the toolbar was positioned before the axis settled.

- [ ] **Step 3: Minimal implementation (guarantee re-position after data settles)**
Append a toolbar re-tuck at the tail of `show_upto`. Read the current end of `show_upto` first; the method begins at `chart.py:1718`. Locate its final statement and add, as the last line of `show_upto`:
```python
        # axis label width only settles once data is revealed; re-tuck the pane toolbars so they
        # clear the (now-known) shared right axis.
        if self._pane_host is not None and self._panes_in_visual_order():
            self._refresh_pane_toolbars()
```
*(Insert this guarded by a check so a chart with no panes does nothing. Place it as the very last lines of `show_upto`, after the existing body, preserving indentation at method level.)*

- [ ] **Step 4: Run-to-pass**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_toolbar_clears_right_axis_after_layout -q
```
Expected PASS (1 passed).

- [ ] **Step 5: Commit**
```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): re-tuck pane toolbars after show_upto settles axis width

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 40: Full-file regression (37 baseline + Phase 2) + studio parity

**Files**
- Test only: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (studio parity)**
The studio chart is the second `PriceChart` instance (`StudioChart`/`self.studio_price` in `app.py`). Since both inherit `PriceChart`, build a second instance directly and assert toolbar behavior is per-instance:
```python
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
```

- [ ] **Step 2: Run-to-fail**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_studio_instance_pane_toolbar_parity -q
```
Expected: PASS if all prior tasks landed (per-instance fields were initialized in `__init__`). If it FAILS because `paneDeleteRequested` isn't connected for the second instance, that indicates a class-level wiring bug — fix by confirming `_new_pane` connects on `self` (it does). Run; expected PASS.

- [ ] **Step 3: Minimal implementation**
No production change expected (this is a guard). If the run is red, the only legitimate fix is ensuring `_maximized_pane`/`_saved_sizes` are instance fields set in `PriceChart.__init__` (Task 33) — verify, do not move them to class scope.

- [ ] **Step 4: Run-to-pass (whole file regression — 37 baseline + all Phase 2 tests)**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected PASS: the original 37 tests plus every Phase 1 and Phase 2 test added so far, all green (no regressions from the toolbar / maximize / reorder changes).

- [ ] **Step 5: Commit**
```
git add tests/test_chart_indicators.py
git commit -m "test(chart): studio-instance pane-toolbar parity + full Phase 2 regression

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

<!-- PHASE 2 DONE -->
```

---

## Phase 3 — Settings dialog parity (Inputs / Style / Visibility + Defaults)

### Task 60: `_Indicator` style state — `widths`/`styles` fields + `spec_defaults`

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`_Indicator.__init__` ~l.418; add `spec_defaults` after `__init__`)
- Test: `tests/test_chart_indicators.py` (append)

- [ ] **Step 1: Write failing test**

```python
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
    params, colors, widths, styles = _Indicator.spec_defaults(spec)
    assert params == {p.name: p.default for p in spec.params}
    assert len(colors) == len(spec.outputs)
    assert widths == [1] * len(spec.outputs)
    assert styles == ["solid"] * len(spec.outputs)
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_fresh_add_has_default_widths_and_styles" "tests/test_chart_indicators.py::test_indicator_spec_defaults_single_source" -q
```
Expected FAIL: `AttributeError: '_Indicator' object has no attribute 'widths'` (first test) and `AttributeError: type object '_Indicator' has no attribute 'spec_defaults'` (second test).

- [ ] **Step 3: Minimal implementation**

Current code (`src/vike_trader_app/ui/chart.py` l.418-423):
```python
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.series = {}                 # computed: output label -> full series
        # render handles (set when rendered):
        self.curves = {}                 # overlay/oscillator: output label -> PlotDataItem
        self.pane = None                 # OscillatorPane (oscillator/pairs)
        self.scatter = None              # pattern marker ScatterPlotItem
```
New code:
```python
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.widths = [1] * max(1, len(spec.outputs))    # per-output line width (px)
        self.styles = ["solid"] * max(1, len(spec.outputs))  # per-output line style name
        self.series = {}                 # computed: output label -> full series
        # render handles (set when rendered):
        self.curves = {}                 # overlay/oscillator: output label -> PlotDataItem
        self.pane = None                 # OscillatorPane (oscillator/pairs)
        self.scatter = None              # pattern marker ScatterPlotItem
```

Then add the `spec_defaults` staticmethod immediately after `_Indicator.__init__` (before the `label` property at l.425). Current code:
```python
    @property
    def label(self) -> str:
        """Legend label, TradingView-style: 'RSI 14' (name + non-default param values)."""
        base = _indicator_code(self.name)
```
New code:
```python
    @staticmethod
    def spec_defaults(spec):
        """Single source of truth for the Defaults button and add_indicator seeding:
        (params, colors, widths, styles) at the registry's defaults."""
        n = max(1, len(spec.outputs))
        params = {p.name: p.default for p in spec.params}
        colors = list(_OVERLAY_COLORS[:n])
        widths = [1] * n
        styles = ["solid"] * n
        return params, colors, widths, styles

    @property
    def label(self) -> str:
        """Legend label, TradingView-style: 'RSI 14' (name + non-default param values)."""
        base = _indicator_code(self.name)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_fresh_add_has_default_widths_and_styles" "tests/test_chart_indicators.py::test_indicator_spec_defaults_single_source" -q
```
Expected PASS (2 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): _Indicator.widths/styles fields + spec_defaults single source"
```

---

### Task 61: Module pen helpers — `_pen_style`, `_LINE_STYLES`, `_LINE_WIDTHS`, `_UNSET`

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (module constants after `_TIMEFRAMES` l.37)
- Test: `tests/test_chart_indicators.py` (append)

- [ ] **Step 1: Write failing test**

```python
def test_pen_style_maps_names_to_qt(app):
    from vike_trader_app.ui.chart import _pen_style, _LINE_STYLES, _LINE_WIDTHS, _UNSET
    assert _pen_style("solid") == QtCore.Qt.SolidLine
    assert _pen_style("dashed") == QtCore.Qt.DashLine
    assert _pen_style("dotted") == QtCore.Qt.DotLine
    assert _pen_style("bogus") == QtCore.Qt.SolidLine  # unknown -> solid
    assert [v for _lbl, v in _LINE_STYLES] == ["solid", "dashed", "dotted"]
    assert list(_LINE_WIDTHS) == [1, 2, 3, 4]
    assert _UNSET is not None and _UNSET != [] and _UNSET != {}  # a distinct sentinel
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_pen_style_maps_names_to_qt" -q
```
Expected FAIL: `ImportError: cannot import name '_pen_style' from 'vike_trader_app.ui.chart'`.

- [ ] **Step 3: Minimal implementation**

Current code (`src/vike_trader_app/ui/chart.py` l.33-38):
```python
_TIMEFRAMES = [
    ("Minutes", [("1m", "1m"), ("3m", "3m"), ("5m", "5m"), ("15m", "15m"), ("30m", "30m")]),
    ("Hours", [("1h", "1h"), ("2h", "2h"), ("4h", "4h")]),
    ("Days", [("1D", "1d"), ("1W", "1w")]),
]


class CandlestickItem(pg.GraphicsObject):
```
New code:
```python
_TIMEFRAMES = [
    ("Minutes", [("1m", "1m"), ("3m", "3m"), ("5m", "5m"), ("15m", "15m"), ("30m", "30m")]),
    ("Hours", [("1h", "1h"), ("2h", "2h"), ("4h", "4h")]),
    ("Days", [("1D", "1d"), ("1W", "1w")]),
]
# Line-style picker (Style tab): (label, name) — name persists on _Indicator.styles.
_LINE_STYLES = [("Solid", "solid"), ("Dashed", "dashed"), ("Dotted", "dotted")]
_LINE_WIDTHS = [1, 2, 3, 4]  # line-width picker (px)
# Distinct sentinel for _apply_edit optional args (NOT falsy — an empty list/dict is a real value).
_UNSET = object()


def _pen_style(name):
    """Map a style name (solid/dashed/dotted) to a Qt.PenStyle; unknown -> SolidLine."""
    return {
        "solid": QtCore.Qt.SolidLine,
        "dashed": QtCore.Qt.DashLine,
        "dotted": QtCore.Qt.DotLine,
    }.get(name, QtCore.Qt.SolidLine)


class CandlestickItem(pg.GraphicsObject):
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_pen_style_maps_names_to_qt" -q
```
Expected PASS (1 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): module _pen_style helper + _LINE_STYLES/_LINE_WIDTHS/_UNSET"
```

---

### Task 62: Apply width/style at the two pen sites (`_render` overlay + `OscillatorPane._build_curves`)

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`OscillatorPane._build_curves` l.907-912; `_render` overlay branch l.1372-1374)
- Test: `tests/test_chart_indicators.py` (append)

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_render_pens_use_width_and_style_overlay" "tests/test_chart_indicators.py::test_build_curves_pens_use_width_and_style_oscillator" -q
```
Expected FAIL: both assert `pen.width() == 3/4` but pens are still built with `width=1`/`SolidLine`, so `pen.width() == 1` and `pen.style() == Qt.SolidLine`.

- [ ] **Step 3: Minimal implementation**

Current code — `OscillatorPane._build_curves` (`src/vike_trader_app/ui/chart.py` l.907-912):
```python
    def _build_curves(self, ind: "_Indicator"):
        cs = {}
        for i, label in enumerate(ind.series):
            col = ind.colors[i % len(ind.colors)]
            cs[label] = self.plot([], [], pen=pg.mkPen(col, width=1))
        self._curves[ind.uid] = cs
```
New code:
```python
    def _build_curves(self, ind: "_Indicator"):
        cs = {}
        widths = getattr(ind, "widths", [1])
        styles = getattr(ind, "styles", ["solid"])
        for i, label in enumerate(ind.series):
            col = ind.colors[i % len(ind.colors)]
            pen = pg.mkPen(col, width=widths[i % len(widths)],
                           style=_pen_style(styles[i % len(styles)]))
            cs[label] = self.plot([], [], pen=pen)
        self._curves[ind.uid] = cs
```

Current code — `_render` overlay branch (`src/vike_trader_app/ui/chart.py` l.1369-1381):
```python
    def _render(self, ind: "_Indicator"):
        if ind.kind == "overlay":
            ind.curves = {}
            for i, lbl in enumerate(ind.series):
                col = ind.colors[i % len(ind.colors)]
                curve = pg.PlotDataItem([], [], pen=pg.mkPen(col, width=1))
                if ind.own_scale:                 # independent right scale (secondary viewbox)
                    self._ensure_vb2()
                    self._vb2.addItem(curve)
                else:
                    self.addItem(curve)
                    curve.setZValue(self._next_z())  # overlays sit above the candles
                ind.curves[lbl] = curve
```
New code:
```python
    def _render(self, ind: "_Indicator"):
        if ind.kind == "overlay":
            ind.curves = {}
            widths = getattr(ind, "widths", [1])
            styles = getattr(ind, "styles", ["solid"])
            for i, lbl in enumerate(ind.series):
                col = ind.colors[i % len(ind.colors)]
                pen = pg.mkPen(col, width=widths[i % len(widths)],
                               style=_pen_style(styles[i % len(styles)]))
                curve = pg.PlotDataItem([], [], pen=pen)
                if ind.own_scale:                 # independent right scale (secondary viewbox)
                    self._ensure_vb2()
                    self._vb2.addItem(curve)
                else:
                    self.addItem(curve)
                    curve.setZValue(self._next_z())  # overlays sit above the candles
                ind.curves[lbl] = curve
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_render_pens_use_width_and_style_overlay" "tests/test_chart_indicators.py::test_build_curves_pens_use_width_and_style_oscillator" -q
```
Expected PASS (2 passed).

- [ ] **Step 6: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): apply per-output line width/style at overlay + oscillator pen sites"
```

---

### Task 63: Shared all-intervals helper (`_all_intervals` + `_normalize_intervals`) used by toggle

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`_toggle_interval_visibility` l.1680-1689; add 2 static helpers above it)
- Test: `tests/test_chart_indicators.py` (append)

- [ ] **Step 1: Write failing test**

```python
def test_all_intervals_and_normalize_helpers(app):
    from vike_trader_app.ui.chart import _all_intervals, _normalize_intervals, _TIMEFRAMES
    expected = [iv for _sec, items in _TIMEFRAMES for _lbl, iv in items]
    assert _all_intervals() == expected
    # every interval checked -> None (shows on all)
    assert _normalize_intervals(set(expected)) is None
    # a strict subset stays a set
    sub = set(expected[:-1])
    assert _normalize_intervals(sub) == sub
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_all_intervals_and_normalize_helpers" -q
```
Expected FAIL: `ImportError: cannot import name '_all_intervals' from 'vike_trader_app.ui.chart'`.

- [ ] **Step 3: Minimal implementation**

Add module-level helpers right after the `_pen_style` function defined in Task 61. Current code (after Task 61, the `_pen_style` block followed by `class CandlestickItem`):
```python
def _pen_style(name):
    """Map a style name (solid/dashed/dotted) to a Qt.PenStyle; unknown -> SolidLine."""
    return {
        "solid": QtCore.Qt.SolidLine,
        "dashed": QtCore.Qt.DashLine,
        "dotted": QtCore.Qt.DotLine,
    }.get(name, QtCore.Qt.SolidLine)


class CandlestickItem(pg.GraphicsObject):
```
New code:
```python
def _pen_style(name):
    """Map a style name (solid/dashed/dotted) to a Qt.PenStyle; unknown -> SolidLine."""
    return {
        "solid": QtCore.Qt.SolidLine,
        "dashed": QtCore.Qt.DashLine,
        "dotted": QtCore.Qt.DotLine,
    }.get(name, QtCore.Qt.SolidLine)


def _all_intervals():
    """The flat, ordered list of every supported interval (single source for both the
    per-interval legend menu / Visibility tab and the 'all ⇒ None' normalization)."""
    return [iv for _sec, items in _TIMEFRAMES for _lbl, iv in items]


def _normalize_intervals(chosen):
    """'all checked ⇒ None' rule: None when every interval is selected, else the set."""
    chosen = set(chosen)
    return None if chosen >= set(_all_intervals()) else chosen


class CandlestickItem(pg.GraphicsObject):
```

Now switch `_toggle_interval_visibility` to use them. Current code (`src/vike_trader_app/ui/chart.py` l.1680-1689):
```python
    def _toggle_interval_visibility(self, ind: "_Indicator", interval: str):
        """Toggle whether ``ind`` shows on ``interval``. ``ind.intervals`` is None when it shows
        on all timeframes; otherwise it's the explicit set of allowed timeframes."""
        all_iv = [iv for _sec, items in _TIMEFRAMES for _lbl, iv in items]
        cur = set(ind.intervals) if ind.intervals is not None else set(all_iv)
        cur.discard(interval) if interval in cur else cur.add(interval)
        ind.intervals = None if cur >= set(all_iv) else cur
        self._apply_visibility(ind)
        self._reveal_indicator(ind, self._reveal_index())
        self._refresh_legends()
```
New code:
```python
    def _toggle_interval_visibility(self, ind: "_Indicator", interval: str):
        """Toggle whether ``ind`` shows on ``interval``. ``ind.intervals`` is None when it shows
        on all timeframes; otherwise it's the explicit set of allowed timeframes."""
        all_iv = _all_intervals()
        cur = set(ind.intervals) if ind.intervals is not None else set(all_iv)
        cur.discard(interval) if interval in cur else cur.add(interval)
        ind.intervals = _normalize_intervals(cur)
        self._apply_visibility(ind)
        self._reveal_indicator(ind, self._reveal_index())
        self._refresh_legends()
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_all_intervals_and_normalize_helpers" "tests/test_chart_indicators.py::test_visibility_on_intervals" -q
```
Expected PASS (2 passed — the existing `test_visibility_on_intervals` still green via the refactored helpers).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "refactor(chart): shared _all_intervals/_normalize_intervals helpers for interval visibility"
```

---

### Task 64: Widen `applied` signal + Style tab width/style combos + emit 5-tuple

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`_IndicatorSettings.applied` l.437; `__init__` Style tab l.499-513; `_accept` l.538-544)
- Test: `tests/test_chart_indicators.py` (update `test_settings_emits_params_on_ok` l.234; append new)

- [ ] **Step 1: Write failing test** — update the existing arity test and add Style-tab tests.

Replace the existing `test_settings_emits_params_on_ok` body. Current code (`tests/test_chart_indicators.py` l.234-244):
```python
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
```
New code:
```python
def test_settings_emits_params_on_ok(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    p0 = ind.spec.params[0]
    dlg._param_widgets[p0.name].setValue(9)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals: got.update(
            params=params, colors=colors, widths=widths, styles=styles, intervals=intervals
        )
    )
    dlg._accept()
    assert got["params"][p0.name] == 9
    assert len(got["colors"]) == len(ind.spec.outputs)
    assert len(got["widths"]) == len(ind.spec.outputs)
    assert len(got["styles"]) == len(ind.spec.outputs)


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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_settings_emits_params_on_ok" "tests/test_chart_indicators.py::test_settings_style_tab_combos_round_trip" "tests/test_chart_indicators.py::test_settings_pattern_hides_width_and_style" -q
```
Expected FAIL: `applied` is still a 2-arg signal so the 5-arg connect lambda never receives widths/styles/intervals; `dlg._width_combos` does not exist (`AttributeError`).

- [ ] **Step 3: Minimal implementation** — widen the signal, build the per-output Style rows with combos, and emit the 5-tuple.

First the signal. Current code (`src/vike_trader_app/ui/chart.py` l.433-437):
```python
class _IndicatorSettings(dropdowns.PopupCard):
    """TradingView-style settings: an **Inputs** tab (parameters from the registry spec) and a
    **Style** tab (per-output colour). Emits ``applied(params, colors)`` on Ok."""

    applied = QtCore.Signal(dict, list)
```
New code:
```python
class _IndicatorSettings(dropdowns.PopupCard):
    """TradingView-style settings: **Inputs** (registry params), **Style** (per-output colour +
    line width + line style), and **Visibility** (per-interval) tabs. Emits
    ``applied(params, colors, widths, styles, intervals)`` on Ok."""

    applied = QtCore.Signal(dict, list, list, object)
```

Now the Style tab. We need `ind` available for kind and the dialog must keep `self._ind`. The `__init__` stores `self._spec = ind.spec`; add `self._ind = ind` alongside it. Current code (`src/vike_trader_app/ui/chart.py` l.452):
```python
        self._spec = ind.spec
        card = self.card
```
New code:
```python
        self._spec = ind.spec
        self._ind = ind
        card = self.card
```

Current code — Style tab (`src/vike_trader_app/ui/chart.py` l.499-513):
```python
        # --- Style tab (one colour button per output) ---
        style = QtWidgets.QWidget()
        sform = QtWidgets.QFormLayout(style)
        sform.setContentsMargins(4, 10, 4, 4)
        sform.setSpacing(9)
        self._color_btns = []
        for i, out in enumerate(self._spec.outputs):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(46, 22)
            col = ind.colors[i] if i < len(ind.colors) else _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
            self._set_btn_color(btn, col)
            btn.clicked.connect(lambda _c=False, b=btn: self._pick_color(b))
            self._color_btns.append(btn)
            sform.addRow(out.replace("_", " ").title(), btn)
        tabs.addTab(style, "Style")
```
New code:
```python
        # --- Style tab (per output: colour + line width + line style) ---
        style = QtWidgets.QWidget()
        sform = QtWidgets.QFormLayout(style)
        sform.setContentsMargins(4, 10, 4, 4)
        sform.setSpacing(9)
        self._color_btns = []
        self._width_combos = []
        self._style_combos = []
        is_pattern = ind.kind == "pattern"
        widths = getattr(ind, "widths", [1] * len(self._spec.outputs))
        styles = getattr(ind, "styles", ["solid"] * len(self._spec.outputs))
        for i, out in enumerate(self._spec.outputs):
            btn = QtWidgets.QPushButton()
            btn.setFixedSize(46, 22)
            col = ind.colors[i] if i < len(ind.colors) else _OVERLAY_COLORS[i % len(_OVERLAY_COLORS)]
            self._set_btn_color(btn, col)
            btn.clicked.connect(lambda _c=False, b=btn: self._pick_color(b))
            self._color_btns.append(btn)

            wcb = QtWidgets.QComboBox()
            for w in _LINE_WIDTHS:
                wcb.addItem(f"{w}px", w)  # userData = int width
            cur_w = widths[i] if i < len(widths) else 1
            wcb.setCurrentIndex(max(0, _LINE_WIDTHS.index(cur_w) if cur_w in _LINE_WIDTHS else 0))
            self._width_combos.append(wcb)

            scb = QtWidgets.QComboBox()
            for lbl, nm in _LINE_STYLES:
                scb.addItem(lbl, nm)      # userData = str style name
            cur_s = styles[i] if i < len(styles) else "solid"
            names = [nm for _lbl, nm in _LINE_STYLES]
            scb.setCurrentIndex(names.index(cur_s) if cur_s in names else 0)
            self._style_combos.append(scb)

            roww = QtWidgets.QWidget()
            rowl = QtWidgets.QHBoxLayout(roww)
            rowl.setContentsMargins(0, 0, 0, 0)
            rowl.setSpacing(6)
            rowl.addWidget(btn)
            rowl.addWidget(wcb)
            rowl.addWidget(scb)
            if is_pattern:  # markers use brushes, not pens -> no width/style
                wcb.hide()
                scb.hide()
            sform.addRow(out.replace("_", " ").title(), roww)
        tabs.addTab(style, "Style")
```

Now `_accept`. Current code (`src/vike_trader_app/ui/chart.py` l.538-544):
```python
    def _accept(self):
        params = {}
        for p in self._spec.params:
            params[p.name] = self._param_widgets[p.name].value()
        colors = [b.property("color_hex") for b in self._color_btns]
        self.applied.emit(params, colors)
        self.accept()
```
New code:
```python
    def _accept(self):
        params = {}
        for p in self._spec.params:
            params[p.name] = self._param_widgets[p.name].value()
        colors = [b.property("color_hex") for b in self._color_btns]
        widths = [int(c.currentData()) for c in self._width_combos]
        styles = [str(c.currentData()) for c in self._style_combos]
        intervals = self._chosen_intervals()
        self.applied.emit(params, colors, widths, styles, intervals)
        self.accept()
```

`_chosen_intervals` is added by Task 65 (the Visibility tab). To keep this task independently green, add a minimal placeholder now that returns the indicator's current intervals; Task 65 replaces it with the real tab-reading version. Add this method right after `_accept`. Current code (the `_set_btn_color` static method follows `_accept`, l.527):
Insert before `@staticmethod` `_set_btn_color`. Current code (`src/vike_trader_app/ui/chart.py` l.526-530):
```python
        self.resize(340, 360)

    @staticmethod
    def _set_btn_color(btn, color):
```
New code:
```python
        self.resize(360, 440)

    def _chosen_intervals(self):
        """Intervals selected in the Visibility tab (Task 65 builds the tab); until then,
        fall back to the indicator's current intervals so accept always has a value."""
        boxes = getattr(self, "_iv_checks", None)
        if not boxes:
            return getattr(self._ind, "intervals", None)
        return _normalize_intervals(iv for iv, cb in boxes.items() if cb.isChecked())

    @staticmethod
    def _set_btn_color(btn, color):
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_settings_emits_params_on_ok" "tests/test_chart_indicators.py::test_settings_style_tab_combos_round_trip" "tests/test_chart_indicators.py::test_settings_pattern_hides_width_and_style" "tests/test_chart_indicators.py::test_settings_builds_input_widgets" -q
```
Expected PASS (4 passed). Note: `edit_indicator`'s `applied.connect` lambda still has 2 args and will be widened in Task 67 — the dialog tests here connect their own 5-arg lambdas, so they pass now.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): widen settings applied signal + per-output width/style combos in Style tab"
```

---

### Task 65: Visibility tab (per-interval checkboxes) + real `_chosen_intervals`

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`_IndicatorSettings.__init__` — add Visibility tab after Style tab l.513; replace `_chosen_intervals` placeholder)
- Test: `tests/test_chart_indicators.py` (append)

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_settings_visibility_tab_covers_all_intervals" "tests/test_chart_indicators.py::test_settings_visibility_uncheck_emits_subset" "tests/test_chart_indicators.py::test_settings_visibility_all_checked_emits_none" "tests/test_chart_indicators.py::test_settings_visibility_seeds_from_existing_set" -q
```
Expected FAIL: `dlg._iv_checks` does not exist yet (`AttributeError`).

- [ ] **Step 3: Minimal implementation** — add the Visibility tab right after the Style tab is added, and replace the placeholder `_chosen_intervals`.

Current code (the line that adds the Style tab, `src/vike_trader_app/ui/chart.py` — after Task 64, the Style block ends with):
```python
            sform.addRow(out.replace("_", " ").title(), roww)
        tabs.addTab(style, "Style")

        foot = QtWidgets.QHBoxLayout()
```
New code:
```python
            sform.addRow(out.replace("_", " ").title(), roww)
        tabs.addTab(style, "Style")

        # --- Visibility tab (per-interval checkboxes, grouped by section) ---
        vis = QtWidgets.QWidget()
        vform = QtWidgets.QVBoxLayout(vis)
        vform.setContentsMargins(4, 10, 4, 4)
        vform.setSpacing(4)
        self._iv_checks = {}
        cur_intervals = getattr(ind, "intervals", None)
        for sec, items in _TIMEFRAMES:
            seclbl = QtWidgets.QLabel(sec.upper())
            seclbl.setStyleSheet(
                f"color:{theme.TEXT3};font-size:10px;font-weight:700;letter-spacing:1px;"
                f"background:transparent;margin-top:6px;"
            )
            vform.addWidget(seclbl)
            for lbl, iv in items:
                cb = QtWidgets.QCheckBox(lbl)
                cb.setStyleSheet(f"color:{theme.TEXT2};background:transparent;")
                cb.setChecked(cur_intervals is None or iv in cur_intervals)
                self._iv_checks[iv] = cb
                vform.addWidget(cb)
        vform.addStretch(1)
        tabs.addTab(vis, "Visibility")

        foot = QtWidgets.QHBoxLayout()
```

Now replace the placeholder `_chosen_intervals` from Task 64 with the real (now-always-present) version. Current code (`src/vike_trader_app/ui/chart.py`, the placeholder added in Task 64):
```python
    def _chosen_intervals(self):
        """Intervals selected in the Visibility tab (Task 65 builds the tab); until then,
        fall back to the indicator's current intervals so accept always has a value."""
        boxes = getattr(self, "_iv_checks", None)
        if not boxes:
            return getattr(self._ind, "intervals", None)
        return _normalize_intervals(iv for iv, cb in boxes.items() if cb.isChecked())
```
New code:
```python
    def _chosen_intervals(self):
        """Intervals selected in the Visibility tab, normalized (all ⇒ None)."""
        return _normalize_intervals(
            iv for iv, cb in self._iv_checks.items() if cb.isChecked()
        )
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_settings_visibility_tab_covers_all_intervals" "tests/test_chart_indicators.py::test_settings_visibility_uncheck_emits_subset" "tests/test_chart_indicators.py::test_settings_visibility_all_checked_emits_none" "tests/test_chart_indicators.py::test_settings_visibility_seeds_from_existing_set" -q
```
Expected PASS (4 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): Visibility tab with per-interval checkboxes grouped by section"
```

---

### Task 66: Defaults button (form-only reset, no emit/close)

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`_IndicatorSettings.__init__` footer l.515-524; add `_reset_defaults` method)
- Test: `tests/test_chart_indicators.py` (append)

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_settings_defaults_button_resets_form_without_emitting" -q
```
Expected FAIL: `AttributeError: '_IndicatorSettings' object has no attribute '_reset_defaults'`.

- [ ] **Step 3: Minimal implementation** — add a Defaults button to the footer and the reset method.

Current code — footer (`src/vike_trader_app/ui/chart.py` l.515-525, after the Visibility tab was added in Task 65 the footer block is unchanged):
```python
        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("Ok")
        ok.setObjectName("ok")
        ok.clicked.connect(self._accept)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)
```
New code:
```python
        foot = QtWidgets.QHBoxLayout()
        defaults = QtWidgets.QPushButton("Defaults")
        defaults.clicked.connect(self._reset_defaults)
        foot.addWidget(defaults)
        foot.addStretch(1)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QtWidgets.QPushButton("Ok")
        ok.setObjectName("ok")
        ok.clicked.connect(self._accept)
        foot.addWidget(cancel)
        foot.addWidget(ok)
        v.addLayout(foot)
```

Add the `_reset_defaults` method right after `_chosen_intervals`. Current code (`src/vike_trader_app/ui/chart.py`, the real `_chosen_intervals` from Task 65 followed by `_set_btn_color`):
```python
    def _chosen_intervals(self):
        """Intervals selected in the Visibility tab, normalized (all ⇒ None)."""
        return _normalize_intervals(
            iv for iv, cb in self._iv_checks.items() if cb.isChecked()
        )

    @staticmethod
    def _set_btn_color(btn, color):
```
New code:
```python
    def _chosen_intervals(self):
        """Intervals selected in the Visibility tab, normalized (all ⇒ None)."""
        return _normalize_intervals(
            iv for iv, cb in self._iv_checks.items() if cb.isChecked()
        )

    def _reset_defaults(self):
        """Repopulate all three tabs from the registry defaults — form-only, no emit/close
        (matches TradingView's Defaults ▾ → Reset settings)."""
        params, colors, widths, styles = _Indicator.spec_defaults(self._spec)
        for p in self._spec.params:
            self._param_widgets[p.name].setValue(params[p.name])
        for i, btn in enumerate(self._color_btns):
            self._set_btn_color(btn, colors[i % len(colors)])
        for i, cb in enumerate(self._width_combos):
            w = widths[i % len(widths)]
            cb.setCurrentIndex(_LINE_WIDTHS.index(w) if w in _LINE_WIDTHS else 0)
        names = [nm for _lbl, nm in _LINE_STYLES]
        for i, cb in enumerate(self._style_combos):
            nm = styles[i % len(styles)]
            cb.setCurrentIndex(names.index(nm) if nm in names else 0)
        for cb in self._iv_checks.values():  # default visibility = every interval
            cb.setChecked(True)

    @staticmethod
    def _set_btn_color(btn, color):
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_settings_defaults_button_resets_form_without_emitting" -q
```
Expected PASS (1 passed).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): settings Defaults button (form-only reset, no emit/close)"
```

---

### Task 67: Widen `_apply_edit` + `edit_indicator` lambda; apply intervals in both branches

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`edit_indicator` lambda l.1492; `_apply_edit` l.1495-1509)
- Test: `tests/test_chart_indicators.py` (append; verify `test_edit_params_recomputes`/`test_edit_colors_applied` stay green)

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_apply_edit_sets_width_style_intervals_overlay" "tests/test_chart_indicators.py::test_apply_edit_intervals_apply_in_oscillator_branch" "tests/test_chart_indicators.py::test_apply_edit_width_style_oscillator_pen" "tests/test_chart_indicators.py::test_apply_edit_positional_callers_still_work" -q
```
Expected FAIL: `_apply_edit` takes only `(uid, params, colors)` so the `widths=`/`styles=`/`intervals=` kwargs raise `TypeError: _apply_edit() got an unexpected keyword argument 'widths'`; the oscillator-branch interval test fails because `_sync_shown` is never called there.

- [ ] **Step 3: Minimal implementation** — widen the lambda and `_apply_edit`.

Current code — `edit_indicator` lambda (`src/vike_trader_app/ui/chart.py` l.1490-1493):
```python
        dlg = _IndicatorSettings(ind, self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.applied.connect(lambda params, colors, u=uid: self._apply_edit(u, params, colors))
        dlg.exec()
```
New code:
```python
        dlg = _IndicatorSettings(ind, self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.applied.connect(
            lambda params, colors, widths, styles, intervals, u=uid: self._apply_edit(
                u, params, colors, widths=widths, styles=styles, intervals=intervals
            )
        )
        dlg.exec()
```

Current code — `_apply_edit` (`src/vike_trader_app/ui/chart.py` l.1495-1509):
```python
    def _apply_edit(self, uid: int, params: dict, colors: list):
        ind = self._indicators.get(uid)
        if ind is None:
            return
        ind.params = params
        ind.colors = colors or ind.colors
        if ind.kind in ("oscillator", "pairs") and ind.pane is not None:
            self._compute(ind)
            ind.pane.update_ind(ind)
            ind.pane.reveal(self._reveal_index())
        else:
            self._unrender(ind)
            self._compute(ind)
            self._render(ind)
        self._refresh_legends()
```
New code:
```python
    def _apply_edit(self, uid: int, params: dict, colors: list,
                    widths=_UNSET, styles=_UNSET, intervals=_UNSET):
        ind = self._indicators.get(uid)
        if ind is None:
            return
        ind.params = params
        ind.colors = colors or ind.colors
        if widths is not _UNSET:
            ind.widths = widths
        if styles is not _UNSET:
            ind.styles = styles
        if intervals is not _UNSET:
            ind.intervals = intervals
        if ind.kind in ("oscillator", "pairs") and ind.pane is not None:
            self._compute(ind)
            ind.pane.update_ind(ind)
            ind.pane.reveal(self._reveal_index())
        else:
            self._unrender(ind)
            self._compute(ind)
            self._render(ind)
        # interval/visibility ALWAYS recomputed in BOTH branches (the oscillator branch above
        # never recomputes ind.shown, so an interval edit would otherwise wait for a timeframe
        # change). Order: _sync_shown -> _apply_visibility -> _reveal_indicator.
        self._sync_shown(ind)
        self._apply_visibility(ind)
        self._reveal_indicator(ind, self._reveal_index())
        self._refresh_legends()
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_apply_edit_sets_width_style_intervals_overlay" "tests/test_chart_indicators.py::test_apply_edit_intervals_apply_in_oscillator_branch" "tests/test_chart_indicators.py::test_apply_edit_width_style_oscillator_pen" "tests/test_chart_indicators.py::test_apply_edit_positional_callers_still_work" "tests/test_chart_indicators.py::test_edit_params_recomputes" "tests/test_chart_indicators.py::test_edit_colors_applied" -q
```
Expected PASS (6 passed — the two existing positional-caller tests stay green via the `_UNSET` guards).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): widen _apply_edit (width/style/intervals) + always re-sync shown in both branches"
```

---

### Task 68: `clone_indicator` forwards width/style/intervals + end-to-end edit_indicator wiring + full-file regression

**Files:**
- Modify: `src/vike_trader_app/ui/chart.py` (`clone_indicator` l.1511-1519)
- Test: `tests/test_chart_indicators.py` (append; verify clone test l.278 + full file green)

- [ ] **Step 1: Write failing test**

```python
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
        lambda params, colors, widths, styles, intervals, u=ind.uid: pc._apply_edit(
            u, params, colors, widths=widths, styles=styles, intervals=intervals
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_clone_copies_width_style_intervals" "tests/test_chart_indicators.py::test_edit_indicator_dialog_round_trip_applies" -q
```
Expected FAIL: `clone_indicator` calls `_apply_edit(clone.uid, ..., list(ind.colors))` without widths/styles/intervals, so the clone keeps default `[1]`/`['solid']`/`None` — `assert clone.widths == [3]` fails.

- [ ] **Step 3: Minimal implementation** — forward the full style + intervals through clone.

Current code — `clone_indicator` (`src/vike_trader_app/ui/chart.py` l.1511-1519):
```python
    def clone_indicator(self, uid: int):
        """Duplicate an indicator (same params/colours) — TradingView's 'Clone'."""
        ind = self._indicators.get(uid)
        if ind is None:
            return None
        clone = self.add_indicator(ind.name, params=dict(ind.params), benchmark=ind.benchmark)
        if clone is not None and ind.colors:
            self._apply_edit(clone.uid, dict(clone.params), list(ind.colors))
        return clone
```
New code:
```python
    def clone_indicator(self, uid: int):
        """Duplicate an indicator (same params/colours/width/style/intervals) — TV's 'Clone'."""
        ind = self._indicators.get(uid)
        if ind is None:
            return None
        clone = self.add_indicator(ind.name, params=dict(ind.params), benchmark=ind.benchmark)
        if clone is not None:
            self._apply_edit(
                clone.uid, dict(clone.params), list(ind.colors),
                widths=list(getattr(ind, "widths", clone.widths)),
                styles=list(getattr(ind, "styles", clone.styles)),
                intervals=(set(ind.intervals) if ind.intervals is not None else None),
            )
        return clone
```

- [ ] **Step 4: Run-to-pass** (named tests + full-file regression)

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_clone_copies_width_style_intervals" "tests/test_chart_indicators.py::test_edit_indicator_dialog_round_trip_applies" "tests/test_chart_indicators.py::test_clone_duplicates_indicator" -q
```
Then the full file (must stay green — original 37 baseline + Phase 1/2/3 additions):
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected PASS (all passed, no failures).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): clone forwards width/style/intervals + full settings-dialog regression green"
```
