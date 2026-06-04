# Deferred Chart Indicators Implementation Plan (Source, Bands, Crosshair)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the three features deferred from PR #78 — a Source dropdown (close/open/high/low/hl2/hlc3/ohlc4/hlcc4), editable threshold bands (RSI 70/30/50, etc.), and a cross-pane crosshair — all to TradingView/TradeLocker parity.

**Architecture:** All changes are in `src/vike_trader_app/ui/chart.py` — **no `core/indicators/base.py` change** (verified: the registry routes inputs generically and serializes only numeric metadata). Source is a chart-layer input remap in `_compute`; bands are a chart-layer `_INDICATOR_BANDS` table seeded onto per-instance `_Indicator.bands` and drawn as `InfiniteLine`s kept out of `self._curves`; the crosshair is a `PriceChart` fan-out broadcasting a snapped bar-index x to every pane. Build order **A (crosshair) → B (source) → C (bands)**; B and C each widen the `_IndicatorSettings.applied` signal once (`+source:str`, then `+bands:list`).

**Tech Stack:** PySide6 (Qt), pyqtgraph 0.14.0, pytest (offscreen).

**Spec:** `docs/superpowers/specs/2026-06-04-chart-indicators-deferred-design.md`. Stacked on PR #78 (`feat/chart-indicators-tv-parity`).

**Test invocation (offscreen set in the test file):**
```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```

---

## Phase A — Cross-pane crosshair

### Task 1: Extract the inline tag QSS to a module-level `_TAG_QSS` constant

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_tag_qss_is_module_const_and_reused -q
  ```
  Expected reason: `AttributeError: module 'vike_trader_app.ui.chart' has no attribute '_TAG_QSS'` (the constant does not exist yet).

- [ ] **Step 3: Minimal implementation**

  In `chart.py`, the module-level constant block currently ends with the `_UNSET` sentinel. Match this EXACT current code:

  ```python
  # Distinct sentinel for _apply_edit optional args (NOT falsy — an empty list/dict is a real value).
  _UNSET = object()
  ```

  Replace it with (append the new constant right after `_UNSET`):

  ```python
  # Distinct sentinel for _apply_edit optional args (NOT falsy — an empty list/dict is a real value).
  _UNSET = object()
  # Crosshair axis-tag style (hovered price/time read-outs) — shared by the price pane AND every
  # oscillator pane so the cross-pane crosshair tags match the price-pane tags pixel-for-pixel.
  _TAG_QSS = (f"color:{theme.TEXT};background:{theme.BORDER};border-radius:2px;padding:0 4px;"
              f"font-family:{theme.FONT_MONO};font-size:10px;")
  ```

  Now reuse it in `PriceChart.__init__`. Match this EXACT current code:

  ```python
          # crosshair axis tag boxes — hovered price on the right axis, time on the bottom axis
          _tag_qss = (f"color:{theme.TEXT};background:{theme.BORDER};border-radius:2px;padding:0 4px;"
                      f"font-family:{theme.FONT_MONO};font-size:10px;")
          self._cx_price_tag = QtWidgets.QLabel(self)
          self._cx_time_tag = QtWidgets.QLabel(self)
          for _tag in (self._cx_price_tag, self._cx_time_tag):
              _tag.setStyleSheet(_tag_qss)
              _tag.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
              _tag.hide()
  ```

  Replace with (drop the local `_tag_qss`, use the module const):

  ```python
          # crosshair axis tag boxes — hovered price on the right axis, time on the bottom axis
          self._cx_price_tag = QtWidgets.QLabel(self)
          self._cx_time_tag = QtWidgets.QLabel(self)
          for _tag in (self._cx_price_tag, self._cx_time_tag):
              _tag.setStyleSheet(_TAG_QSS)
              _tag.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
              _tag.hide()
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_tag_qss_is_module_const_and_reused -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
refactor(chart): extract crosshair tag QSS to module const _TAG_QSS

Lift the inline price-pane tag style into a shared _TAG_QSS so the
upcoming cross-pane crosshair's per-pane value/time tags match the
price-pane tags exactly. No behaviour change.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 2: `OscillatorPane` gains crosshair items (`_cx_v` + value/time tags) + signals + scene-move hook

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_pane_has_crosshair_items_and_signals -q
  ```
  Expected reason: `AttributeError: 'OscillatorPane' object has no attribute '_cx_v'` (the crosshair items/signals don't exist yet).

- [ ] **Step 3: Minimal implementation**

  First add the two new signals to `OscillatorPane`. Match this EXACT current code (the signal block in `OscillatorPane`):

  ```python
      dragMoved = QtCore.Signal(object, int)  # (pane, cursor global y) — drag-to-reorder
      dragEnded = QtCore.Signal()
      # pane-level (carry the pane, so a multi-indicator merged pane moves/deletes atomically)
      paneMoveUp = QtCore.Signal(object)
      paneMoveDown = QtCore.Signal(object)
      paneMaximizeToggled = QtCore.Signal(object)
      paneDeleteRequested = QtCore.Signal(object)
  ```

  Replace with (append the two crosshair fan-out signals):

  ```python
      dragMoved = QtCore.Signal(object, int)  # (pane, cursor global y) — drag-to-reorder
      dragEnded = QtCore.Signal()
      # pane-level (carry the pane, so a multi-indicator merged pane moves/deletes atomically)
      paneMoveUp = QtCore.Signal(object)
      paneMoveDown = QtCore.Signal(object)
      paneMaximizeToggled = QtCore.Signal(object)
      paneDeleteRequested = QtCore.Signal(object)
      # cross-pane crosshair: a hover anywhere in this pane fans a bar-index x out to the price
      # chart (which re-fans to every other pane); leaving the pane clears the whole crosshair.
      crosshairMoved = QtCore.Signal(float)
      crosshairLeft = QtCore.Signal()
  ```

  Now add the crosshair items + scene hook at the END of `OscillatorPane.__init__`. Match this EXACT current code (the tail of `__init__`, after the toolbar timer wiring):

  ```python
          # belt-and-braces: re-check cursor-in-rect before hiding so the bar survives a menu/popup.
          self._tb_timer = QtCore.QTimer(self)
          self._tb_timer.setInterval(120)
          self._tb_timer.setSingleShot(True)
          self._tb_timer.timeout.connect(self._maybe_hide_toolbar)
  ```

  Replace with (append the crosshair setup; the toolbar block is preserved verbatim):

  ```python
          # belt-and-braces: re-check cursor-in-rect before hiding so the bar survives a menu/popup.
          self._tb_timer = QtCore.QTimer(self)
          self._tb_timer.setInterval(120)
          self._tb_timer.setSingleShot(True)
          self._tb_timer.timeout.connect(self._maybe_hide_toolbar)

          # cross-pane crosshair: a vertical line the price chart drives across every pane, plus a
          # value tag (this pane's right scale) and a time tag (lowest pane only). ignoreBounds is
          # MANDATORY — else the line forces this pane's x-range. _cx_bar caches the snapped bar x
          # so repeated fan-outs at the same bar skip the FullViewportUpdate repaint.
          cx_pen = pg.mkPen(theme.TEXT2, width=1, style=QtCore.Qt.DashLine)
          self._cx_v = pg.InfiniteLine(angle=90, movable=False, pen=cx_pen)
          self.addItem(self._cx_v, ignoreBounds=True)
          self._cx_v.hide()
          self._cx_bar = None  # last snapped round(x); guards redundant repaints
          self._cx_val_tag = QtWidgets.QLabel(self)
          self._cx_time_tag = QtWidgets.QLabel(self)
          for _tag in (self._cx_val_tag, self._cx_time_tag):
              _tag.setStyleSheet(_TAG_QSS)
              _tag.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
              _tag.hide()
          self.scene().sigMouseMoved.connect(self._on_pane_mouse_moved)
  ```

  `_on_pane_mouse_moved`, `set_crosshair_x`, `clear_crosshair`, and `set_time_tag` are added in the next tasks; this task only proves the items/signals exist, so add a minimal stub for `_on_pane_mouse_moved` directly after `leaveEvent` so the `sigMouseMoved` connection has a target. Match this EXACT current code (`OscillatorPane.leaveEvent`):

  ```python
      def leaveEvent(self, e):  # noqa: N802 - Qt override
          # hide immediately when cursor is out; arm timer if cursor moved onto a child/popup
          if self._cursor_in_rect():
              self._tb_timer.start()   # cursor moved onto a child/popup: re-check shortly
          else:
              self._toolbar.hide()
          if e is not None:
              super().leaveEvent(e)
  ```

  Replace with (append the stub `_on_pane_mouse_moved`; `leaveEvent` body unchanged for now — Task 5 extends it):

  ```python
      def leaveEvent(self, e):  # noqa: N802 - Qt override
          # hide immediately when cursor is out; arm timer if cursor moved onto a child/popup
          if self._cursor_in_rect():
              self._tb_timer.start()   # cursor moved onto a child/popup: re-check shortly
          else:
              self._toolbar.hide()
          if e is not None:
              super().leaveEvent(e)

      def _on_pane_mouse_moved(self, scene_pos):
          """Crosshair fan-out for a hover inside this pane (wired in Task 5)."""
          return
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_pane_has_crosshair_items_and_signals -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): OscillatorPane crosshair items + fan-out signals

Add the per-pane vertical crosshair line (ignoreBounds so it never
forces the pane x-range), hidden value/time tags styled from _TAG_QSS,
the crosshairMoved/crosshairLeft signals, and the scene mouse-move hook
(stub body for now). Wired in the following tasks.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 3: `OscillatorPane.set_crosshair_x` / `clear_crosshair` / `set_time_tag`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
def test_pane_set_and_clear_crosshair(app):
    pc, split = _chart(app)
    split.resize(900, 700)
    split.show()
    app.processEvents()
    ind = pc.add_indicator("rsi")
    pane = ind.pane
    pane.resize(400, 140)
    # set: line snaps to the rounded bar index, shows, value tag un-hidden on the right edge
    pane.set_crosshair_x(12.4)
    assert pane._cx_v.isVisible() is True
    assert pane._cx_v.value() == 12          # snapped to round(x)
    assert pane._cx_bar == 12
    assert not pane._cx_val_tag.isHidden()   # value tag shown (offscreen -> use not isHidden())
    # value tag sits left of the right price axis (its right edge clears the axis labels)
    axis_w = int(pane.getAxis("right").width())
    assert pane._cx_val_tag.x() + pane._cx_val_tag.width() <= pane.width() - axis_w + 1
    # repeated set at the SAME bar is throttled: bar cache unchanged, line stays put
    pane.set_crosshair_x(12.0)
    assert pane._cx_bar == 12 and pane._cx_v.value() == 12
    # a new bar moves the line
    pane.set_crosshair_x(20.0)
    assert pane._cx_v.value() == 20 and pane._cx_bar == 20
    # clear: line + both tags hidden, bar cache reset
    pane.set_time_tag("06-04 12:00", scene_x=50.0)
    assert not pane._cx_time_tag.isHidden()
    pane.clear_crosshair()
    assert pane._cx_v.isVisible() is False
    assert pane._cx_val_tag.isHidden() and pane._cx_time_tag.isHidden()
    assert pane._cx_bar is None
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_set_and_clear_crosshair -q
  ```
  Expected reason: `AttributeError: 'OscillatorPane' object has no attribute 'set_crosshair_x'` (the method isn't defined yet).

- [ ] **Step 3: Minimal implementation**

  Add the three methods to `OscillatorPane`, right after the `_on_pane_mouse_moved` stub added in Task 2. Match this EXACT current code (the stub from Task 2):

  ```python
      def _on_pane_mouse_moved(self, scene_pos):
          """Crosshair fan-out for a hover inside this pane (wired in Task 5)."""
          return
  ```

  Replace with (keep the stub for now — Task 4 fills its body — and add the three crosshair helpers before it):

  ```python
      def _series_value_at(self, index: int):
          """The hosted indicators' values at bar ``index`` (one per visible curve), for the
          value-tag read-out. Reads ind.series ONLY — never band lines — and skips None/warm-up."""
          vals = []
          for ind in self._inds:
              if not ind.shown:
                  continue
              for label in self._curves.get(ind.uid, {}):
                  series = ind.series.get(label, [])
                  if 0 <= index < len(series) and series[index] is not None:
                      vals.append(series[index])
          return vals

      def set_crosshair_x(self, x):
          """Snap the pane's vertical crosshair to round(x) and place a value tag at the right
          scale. Skips the repaint when the snapped bar is unchanged (FullViewportUpdate repaints
          the whole pane on every move)."""
          bar = int(round(x))
          if bar == self._cx_bar and self._cx_v.isVisible():
              return
          self._cx_bar = bar
          self._cx_v.setPos(bar)
          self._cx_v.show()
          vals = self._series_value_at(bar)
          if not vals:
              self._cx_val_tag.hide()
              return
          val = vals[0]
          self._cx_val_tag.setText(f"{val:,.2f}")
          self._cx_val_tag.adjustSize()
          axis_w = int(self.getAxis("right").width()) if self.getAxis("right").isVisible() else 0
          scene_pt = self.getViewBox().mapViewToScene(QtCore.QPointF(bar, val))
          x_px = self.width() - axis_w - self._cx_val_tag.width() - 1
          self._cx_val_tag.move(max(0, x_px), int(scene_pt.y()) - self._cx_val_tag.height() // 2)
          self._cx_val_tag.show()

      def clear_crosshair(self):
          """Hide this pane's vertical crosshair line + value/time tags (cross-pane clear)."""
          self._cx_v.hide()
          self._cx_val_tag.hide()
          self._cx_time_tag.hide()
          self._cx_bar = None

      def set_time_tag(self, text, scene_x):
          """Bottom-edge time label (lowest pane only). x is in THIS pane's scene coords."""
          self._cx_time_tag.setText(text)
          self._cx_time_tag.adjustSize()
          h = self._cx_time_tag.height()
          self._cx_time_tag.move(int(scene_x) - self._cx_time_tag.width() // 2,
                                 self.height() - h - 1)
          self._cx_time_tag.show()

      def _on_pane_mouse_moved(self, scene_pos):
          """Crosshair fan-out for a hover inside this pane (wired in Task 5)."""
          return
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_set_and_clear_crosshair -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): OscillatorPane set_crosshair_x/clear_crosshair/set_time_tag

set_crosshair_x snaps to round(x), shows the vertical line, and tucks a
value tag (read from ind.series only, never bands) at the right scale,
throttling on an unchanged snapped bar. clear_crosshair hides the line +
both tags. set_time_tag homes a bottom-edge time label using this pane's
own scene mapping.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 4: `OscillatorPane._on_pane_mouse_moved` emits `crosshairMoved`/`crosshairLeft`; `leaveEvent` also emits `crosshairLeft`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_pane_mouse_move_emits_moved_and_left" "tests/test_chart_indicators.py::test_pane_leave_event_emits_crosshair_left_and_keeps_toolbar" -q
  ```
  Expected reason: `test_pane_mouse_move_emits_moved_and_left` fails its `len(moved) == 1` assert (the stub `_on_pane_mouse_moved` emits nothing), and `test_pane_leave_event_emits_crosshair_left_and_keeps_toolbar` fails its `left == [1]` assert (`leaveEvent` doesn't emit `crosshairLeft` yet).

- [ ] **Step 3: Minimal implementation**

  Fill in the `_on_pane_mouse_moved` body. Match this EXACT current code (the stub):

  ```python
      def _on_pane_mouse_moved(self, scene_pos):
          """Crosshair fan-out for a hover inside this pane (wired in Task 5)."""
          return
  ```

  Replace with:

  ```python
      def _on_pane_mouse_moved(self, scene_pos):
          """Per-widget scenes: a hover anywhere in THIS pane maps to a bar-index x and fans out
          via the price chart. Outside the viewbox -> clear the whole crosshair. NB the value tag
          for this (hovered) pane is drawn by set_crosshair_x off the fan-out, not here."""
          vb = self.getViewBox()
          if not vb.sceneBoundingRect().contains(scene_pos):
              self.crosshairLeft.emit()
              return
          self.crosshairMoved.emit(vb.mapSceneToView(scene_pos).x())
  ```

  Now extend `leaveEvent` to also emit `crosshairLeft`, KEEPING the toolbar logic. Match this EXACT current code:

  ```python
      def leaveEvent(self, e):  # noqa: N802 - Qt override
          # hide immediately when cursor is out; arm timer if cursor moved onto a child/popup
          if self._cursor_in_rect():
              self._tb_timer.start()   # cursor moved onto a child/popup: re-check shortly
          else:
              self._toolbar.hide()
          if e is not None:
              super().leaveEvent(e)
  ```

  Replace with:

  ```python
      def leaveEvent(self, e):  # noqa: N802 - Qt override
          # hide immediately when cursor is out; arm timer if cursor moved onto a child/popup
          if self._cursor_in_rect():
              self._tb_timer.start()   # cursor moved onto a child/popup: re-check shortly
          else:
              self._toolbar.hide()
          self.crosshairLeft.emit()    # leaving the pane clears the whole cross-pane crosshair
          if e is not None:
              super().leaveEvent(e)
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_pane_mouse_move_emits_moved_and_left" "tests/test_chart_indicators.py::test_pane_leave_event_emits_crosshair_left_and_keeps_toolbar" -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): OscillatorPane hover emits crosshair fan-out signals

_on_pane_mouse_moved maps a hover to a bar-index x and emits
crosshairMoved (inside the viewbox) or crosshairLeft (outside).
leaveEvent now also emits crosshairLeft while preserving the Phase-2
toolbar hide/timer logic.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 5: `PriceChart._set_crosshair_x` fan-out + `_clear_crosshair`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_price_set_crosshair_fans_to_all_panes" "tests/test_chart_indicators.py::test_price_clear_crosshair_clears_everything" "tests/test_chart_indicators.py::test_price_set_crosshair_no_panes_uses_own_time_tag" -q
  ```
  Expected reason: `AttributeError: 'PriceChart' object has no attribute '_set_crosshair_x'` (the fan-out method isn't defined yet).

- [ ] **Step 3: Minimal implementation**

  Add the two methods to `PriceChart`, immediately before `_on_mouse_moved`. Match this EXACT current code (the start of `_on_mouse_moved`):

  ```python
      def _on_mouse_moved(self, scene_pos):
          if not self._bars:
              return
  ```

  Replace with (insert the two new methods ahead of `_on_mouse_moved`; the `_on_mouse_moved` header is preserved):

  ```python
      def _set_crosshair_x(self, x):
          """Fan a bar-index x out across the whole chart: snap the price-pane vertical line to
          round(x), drive every oscillator pane's vertical line to the same bar, and home the time
          tag on the lowest pane (or, with no panes, the price chart's own bottom-axis tag)."""
          bar = int(round(x))
          self._cx_v.setPos(bar)
          self._cx_v.show()
          panes = self._panes_in_visual_order()
          for p in panes:
              p.set_crosshair_x(bar)
          dt = datetime.fromtimestamp(x_to_ts(self._bars, bar) / 1000, tz=timezone.utc)
          text = dt.strftime("%m-%d %H:%M")
          if panes:
              # time axis lives under the lowest pane -> home the time tag there (its own scene x)
              self._cx_time_tag.hide()
              low = panes[-1]
              scene_x = low.getViewBox().mapViewToScene(QtCore.QPointF(bar, 0.0)).x()
              low.set_time_tag(text, scene_x)
          else:
              scene_x = self.getViewBox().mapViewToScene(QtCore.QPointF(bar, 0.0)).x()
              self._cx_time_tag.setText(text)
              self._cx_time_tag.adjustSize()
              self._cx_time_tag.move(int(scene_x) - self._cx_time_tag.width() // 2,
                                     self.height() - self._cx_time_tag.height() - 1)
              self._cx_time_tag.show()

      def _clear_crosshair(self):
          """Hide the whole cross-pane crosshair: price line/h-line/tags + every pane's, and
          restore the OHLC header to the latest candle."""
          self._cx_v.hide()
          self._cx_h.hide()
          self._cx_price_tag.hide()
          self._cx_time_tag.hide()
          for p in self._panes_in_visual_order():
              p.clear_crosshair()
          self._show_last_ohlc()

      def _on_mouse_moved(self, scene_pos):
          if not self._bars:
              return
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest "tests/test_chart_indicators.py::test_price_set_crosshair_fans_to_all_panes" "tests/test_chart_indicators.py::test_price_clear_crosshair_clears_everything" "tests/test_chart_indicators.py::test_price_set_crosshair_no_panes_uses_own_time_tag" -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): PriceChart _set_crosshair_x fan-out + _clear_crosshair

_set_crosshair_x snaps the price vertical line and drives every pane's
vertical line to the same bar, homing the time tag on the lowest pane
(or the price chart's own bottom-axis tag with no panes).
_clear_crosshair hides every crosshair element and restores the pinned
OHLC header.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 6: Wire `_on_mouse_moved` to the fan-out (IN → `_set_crosshair_x`, OUT → `_clear_crosshair`); re-home the time tag

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py` (inverts the existing hidden-when-panes test)

- [ ] **Step 1: Write failing test** — first INVERT the existing `test_crosshair_time_tag_hidden_when_panes_exist` in `tests/test_chart_indicators.py`. Match this EXACT current code:

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
```

Replace with (the time tag now re-homes to the lowest pane; the price tag stays hidden):

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_crosshair_time_tag_rehomed_to_lowest_pane -q
  ```
  Expected reason: `assert not low._cx_time_tag.isHidden()` fails — the current `_on_mouse_moved` still hides the time tag when panes exist (the Phase-1 block) and never fans out to the pane.

- [ ] **Step 3: Minimal implementation**

  Rewire `_on_mouse_moved`. Match this EXACT current code (the full method body, including the Phase-1 "hide when panes exist" block):

  ```python
      def _on_mouse_moved(self, scene_pos):
          if not self._bars:
              return
          vb = self.getViewBox()
          if not vb.sceneBoundingRect().contains(scene_pos):
              self._cx_v.hide()
              self._cx_h.hide()
              self._cx_price_tag.hide()
              self._cx_time_tag.hide()
              self._show_last_ohlc()
              return
          pt = vb.mapSceneToView(scene_pos)
          self._cx_v.setPos(pt.x())
          self._cx_h.setPos(pt.y())
          self._cx_v.show()
          self._cx_h.show()
          # axis tag boxes (scene coords ≈ widget pixels): price on the right, time on the bottom
          py = int(scene_pos.y())
          self._cx_price_tag.setText(f"{pt.y():,.2f}")
          self._cx_price_tag.adjustSize()
          self._cx_price_tag.move(self.width() - self._cx_price_tag.width() - 1,
                                  py - self._cx_price_tag.height() // 2)
          self._cx_price_tag.show()
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
          # NB: the OHLC header is intentionally NOT updated to the hovered bar — it stays
          # pinned to the latest candle (the crosshair still reads price/time off the axes).
  ```

  Replace with (OUT branch → `_clear_crosshair`; keep the local `_cx_h` + price tag; IN branch → fan out via `_set_crosshair_x`, which re-homes the time tag):

  ```python
      def _on_mouse_moved(self, scene_pos):
          if not self._bars:
              return
          vb = self.getViewBox()
          if not vb.sceneBoundingRect().contains(scene_pos):
              self._clear_crosshair()
              return
          pt = vb.mapSceneToView(scene_pos)
          # local price-pane read-outs: the horizontal segment + the right-scale price tag stay
          # at the real hovered y (the vertical line + time tag are fanned out below).
          self._cx_h.setPos(pt.y())
          self._cx_h.show()
          py = int(scene_pos.y())
          self._cx_price_tag.setText(f"{pt.y():,.2f}")
          self._cx_price_tag.adjustSize()
          self._cx_price_tag.move(self.width() - self._cx_price_tag.width() - 1,
                                  py - self._cx_price_tag.height() // 2)
          self._cx_price_tag.show()
          # fan the snapped bar-x out to the price vertical line, every pane, and the time tag
          # (re-homed onto the lowest pane when panes exist — replaces the Phase-1 hide block).
          self._set_crosshair_x(pt.x())
          # NB: the OHLC header is intentionally NOT updated to the hovered bar — it stays
          # pinned to the latest candle (the crosshair still reads price/time off the axes).
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_crosshair_time_tag_rehomed_to_lowest_pane tests/test_chart_indicators.py::test_crosshair_time_tag_shown_with_no_panes -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): wire price-pane hover to the cross-pane crosshair fan-out

_on_mouse_moved now fans the snapped bar-x out via _set_crosshair_x and
clears the whole crosshair on the out-of-rect branch. Replaces the
Phase-1 "hide time tag when panes exist" block with the lowest-pane
re-home. Keeps the local horizontal segment + right-scale price tag.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 7: `PriceChart.leaveEvent` → `_clear_crosshair`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_price_leave_event_clears_crosshair -q
  ```
  Expected reason: the inherited `pg.PlotWidget.leaveEvent` doesn't clear the crosshair, so `pc._cx_v.isVisible()` is still `True` after `leaveEvent(None)`.

- [ ] **Step 3: Minimal implementation**

  Add a `leaveEvent` override to `PriceChart`, immediately after `resizeEvent`. Match this EXACT current code (the end of `PriceChart.resizeEvent`):

  ```python
          if hasattr(self, "_auto_btn"):
              self._auto_btn.adjustSize()
              self._auto_btn.move(
                  self.width() - self._auto_btn.width() - 8,
                  self.height() - self._auto_btn.height() - 6,
              )
          self._position_price_legend()
  ```

  Replace with (append the `leaveEvent` override after `resizeEvent`):

  ```python
          if hasattr(self, "_auto_btn"):
              self._auto_btn.adjustSize()
              self._auto_btn.move(
                  self.width() - self._auto_btn.width() - 8,
                  self.height() - self._auto_btn.height() - 6,
              )
          self._position_price_legend()

      def leaveEvent(self, e):  # noqa: N802 - Qt override
          # cover the splitter-gutter case: leaving the price widget clears the whole crosshair
          # (the out-of-rect branch in _on_mouse_moved alone can miss a fast exit into the gutter).
          self._clear_crosshair()
          if e is not None:
              super().leaveEvent(e)
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_price_leave_event_clears_crosshair -q
  ```

- [ ] **Step 5: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): PriceChart.leaveEvent clears the cross-pane crosshair

Leaving the price widget clears every crosshair element, covering the
splitter-gutter case the out-of-rect branch can miss on a fast exit.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

### Task 8: `_new_pane` connects `pane.crosshairMoved`/`crosshairLeft` (pane-hover fans back to the price chart)

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test** — append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_hover_fans_to_price_and_other_panes -q
  ```
  Expected reason: `assert pc._cx_v.value() == bar` fails — `_new_pane` doesn't connect `crosshairMoved`/`crosshairLeft` yet, so the pane hover never reaches `_set_crosshair_x`.

- [ ] **Step 3: Minimal implementation**

  Connect the two new signals in `_new_pane`. Match this EXACT current code (the wiring block in `_new_pane`):

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
  ```

  Replace with (add the two crosshair connections; existing wiring preserved):

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
          # a hover inside this pane fans the bar-x out to the price chart, which re-fans to every
          # other pane (and homes the time tag); leaving the pane clears the whole crosshair.
          pane.crosshairMoved.connect(self._set_crosshair_x)
          pane.crosshairLeft.connect(self._clear_crosshair)
          self._pane_host.addWidget(pane)
  ```

- [ ] **Step 4: Run-to-pass**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_pane_hover_fans_to_price_and_other_panes -q
  ```

- [ ] **Step 5: Run the FULL suite to confirm the 109 baseline + the Phase-A additions are all green**
  ```
  PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
  ```
  Expected: all tests pass (the 109 baseline minus the 1 renamed/inverted test, plus the 9 new Phase-A tests).

- [ ] **Step 6: Commit**
  ```
  git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
  git commit -m "$(cat <<'EOF'
feat(chart): pane hover fans the crosshair back to the price chart

_new_pane connects each pane's crosshairMoved -> _set_crosshair_x and
crosshairLeft -> _clear_crosshair, so a hover in any oscillator pane
drives the vertical line across the price chart and every other pane and
clears on leave. Completes Phase A (cross-pane crosshair).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
  ```

---

## Phase B — Source dropdown

### Task 30: Module helpers — `is_source_selectable`, `_SOURCE_OPTIONS`, `_source_series`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (helpers exist, count==53, derived math)**

Append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_is_source_selectable_count_is_53 tests/test_chart_indicators.py::test_is_source_selectable_gates_correctly tests/test_chart_indicators.py::test_source_options_are_the_eight_tv_sources tests/test_chart_indicators.py::test_source_series_raw_and_derived_math -q
```
Expected reason: `ImportError: cannot import name 'is_source_selectable'` (and `_SOURCE_OPTIONS` / `_source_series`) from `vike_trader_app.ui.chart` — the helpers don't exist yet.

- [ ] **Step 3: Minimal implementation**

In `src/vike_trader_app/ui/chart.py`, find the existing `_UNSET` sentinel block (it ends the module-constants section):

```python
# Distinct sentinel for _apply_edit optional args (NOT falsy — an empty list/dict is a real value).
_UNSET = object()
```

Insert directly AFTER it:

```python
# Distinct sentinel for _apply_edit optional args (NOT falsy — an empty list/dict is a real value).
_UNSET = object()

# TradingView price sources for single-series indicators (D1/D2). Default `close`.
_SOURCE_OPTIONS = ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"]


def is_source_selectable(spec) -> bool:
    """True when an indicator takes exactly one OHLC price column (so swapping the source is
    meaningful). All 53 such indicators ship with `close`; pairs (`close`/`benchmark`), volume
    (`obv`, `volume_osc`) and multi-price (stochastic/pattern) inputs are excluded automatically."""
    return len(spec.inputs) == 1 and spec.inputs[0] in {"close", "open", "high", "low"}


def _source_series(data, source):
    """The price series for ``source`` from a `_data_cols`-style column dict: a raw o/h/l/c
    column, or a derived blend — hl2=(h+l)/2, hlc3=(h+l+c)/3, ohlc4=(o+h+l+c)/4, hlcc4=(h+l+2c)/4.
    Pure and list-based to match `_data_cols`. Unknown source falls back to `close`."""
    o, h, l, c = data["open"], data["high"], data["low"], data["close"]
    if source in ("open", "high", "low", "close"):
        return list(data[source])
    if source == "hl2":
        return [(hi + lo) / 2 for hi, lo in zip(h, l)]
    if source == "hlc3":
        return [(hi + lo + cl) / 3 for hi, lo, cl in zip(h, l, c)]
    if source == "ohlc4":
        return [(op + hi + lo + cl) / 4 for op, hi, lo, cl in zip(o, h, l, c)]
    if source == "hlcc4":
        return [(hi + lo + 2 * cl) / 4 for hi, lo, cl in zip(h, l, c)]
    return list(c)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_is_source_selectable_count_is_53 tests/test_chart_indicators.py::test_is_source_selectable_gates_correctly tests/test_chart_indicators.py::test_source_options_are_the_eight_tv_sources tests/test_chart_indicators.py::test_source_series_raw_and_derived_math -q
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add source-dropdown helpers (is_source_selectable, _SOURCE_OPTIONS, _source_series)"
```


### Task 31: `_Indicator.source` field + `spec_defaults` includes `'close'` + label appends `(source)`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (default source, spec_defaults arity, label)**

Append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_indicator_source_defaults_to_close tests/test_chart_indicators.py::test_spec_defaults_includes_close_source tests/test_chart_indicators.py::test_label_appends_non_default_source -q
```
Expected reason: `AttributeError: '_Indicator' object has no attribute 'source'` (default test); `spec_defaults` returns a 4-tuple so unpacking 5 values raises `ValueError: not enough values to unpack`; label has no `(source)` suffix.

- [ ] **Step 3: Minimal implementation**

In `_Indicator.__init__`, match this existing block (the styles line ends the per-output attributes):

```python
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.widths = [1] * max(1, len(spec.outputs))    # per-output line width (px)
        self.styles = ["solid"] * max(1, len(spec.outputs))  # per-output line style name
        self.series = {}                 # computed: output label -> full series
```

Replace with (insert the `source` line):

```python
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.widths = [1] * max(1, len(spec.outputs))    # per-output line width (px)
        self.styles = ["solid"] * max(1, len(spec.outputs))  # per-output line style name
        self.source = "close"            # price source feeding a single-series indicator (D1/D2)
        self.series = {}                 # computed: output label -> full series
```

In `_Indicator.spec_defaults`, match the existing body:

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
```

Replace with:

```python
    @staticmethod
    def spec_defaults(spec):
        """Single source of truth for the Defaults button and add_indicator seeding:
        (params, colors, widths, styles, source) at the registry's defaults."""
        n = max(1, len(spec.outputs))
        params = {p.name: p.default for p in spec.params}
        colors = list(_OVERLAY_COLORS[:n])
        widths = [1] * n
        styles = ["solid"] * n
        source = "close"
        return params, colors, widths, styles, source
```

In `_Indicator.label`, match the existing property:

```python
    @property
    def label(self) -> str:
        """Legend label, TradingView-style: 'RSI 14' (name + non-default param values)."""
        base = _indicator_code(self.name)
        vals = [str(self.params[p.name]) for p in self.spec.params]
        return f"{base} {' '.join(vals)}".strip() if vals else base
```

Replace with:

```python
    @property
    def label(self) -> str:
        """Legend label, TradingView-style: 'RSI 14' (name + non-default param values), with a
        '(source)' suffix when the price source is non-default, e.g. 'RSI 14 (hl2)'."""
        base = _indicator_code(self.name)
        vals = [str(self.params[p.name]) for p in self.spec.params]
        text = f"{base} {' '.join(vals)}".strip() if vals else base
        source = getattr(self, "source", "close")
        if source != "close":
            text = f"{text} ({source})"
        return text
```

- [ ] **Step 4: Run-to-pass**

`spec_defaults` now returns a 5-tuple, so `_reset_defaults` (which unpacks `params, colors, widths, styles = _Indicator.spec_defaults(...)`) will break. To keep the green baseline intact within this task, update `_reset_defaults`'s first line.

Match the existing line in `_reset_defaults`:

```python
        params, colors, widths, styles = _Indicator.spec_defaults(self._spec)
```

Replace with (discard the new source with `_` until Task 33 wires the combo):

```python
        params, colors, widths, styles, _source = _Indicator.spec_defaults(self._spec)
```

Then run:

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_indicator_source_defaults_to_close tests/test_chart_indicators.py::test_spec_defaults_includes_close_source tests/test_chart_indicators.py::test_label_appends_non_default_source -q
```
Expected: 3 passed. Then run the whole file to confirm the 5-tuple change didn't regress the baseline:

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected: all previously-green tests still pass (109 + new).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add _Indicator.source field, seed in spec_defaults, append (source) to legend label"
```


### Task 32: `PriceChart._compute` remaps the input column when source is selectable & non-default

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (hl2-SMA behavioural + default byte-identical regression)**

Append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_compute_remaps_source_hl2_sma tests/test_chart_indicators.py::test_compute_remaps_multi_output_bollinger_on_ohlc4 -q
```
Expected reason: `_compute` ignores `ind.source`, so the `sma` series is still close-fed — `hl2_vals != close_vals` fails (they're equal) and the hand-computed hl2 SMA mismatch fails. (`test_compute_default_source_is_byte_identical` already passes since the close path is unchanged — that's the regression guard.)

- [ ] **Step 3: Minimal implementation**

In `PriceChart._compute`, match the existing data-prep block:

```python
    def _compute(self, ind: "_Indicator"):
        """Run the indicator with its current params -> ``ind.series`` {output label -> series}."""
        from vike_trader_app.core.indicators import base as _base

        data = self._data_cols()
        if ind.kind == "pairs":
            data["benchmark"] = ind.benchmark or []
        try:
            result = _base.compute(ind.name, data, **ind.params)
```

Replace with (insert the single load-bearing remap between the pairs line and the compute call):

```python
    def _compute(self, ind: "_Indicator"):
        """Run the indicator with its current params -> ``ind.series`` {output label -> series}."""
        from vike_trader_app.core.indicators import base as _base

        data = self._data_cols()
        if ind.kind == "pairs":
            data["benchmark"] = ind.benchmark or []
        # Source remap (D1/D2): swap the single input column for the chosen price source. Only
        # for source-selectable indicators and only when non-default — the close path is untouched
        # (zero overhead, byte-identical to pre-source behaviour).
        if is_source_selectable(ind.spec) and getattr(ind, "source", "close") != "close":
            data[ind.spec.inputs[0]] = _source_series(data, ind.source)
        try:
            result = _base.compute(ind.name, data, **ind.params)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_compute_remaps_source_hl2_sma tests/test_chart_indicators.py::test_compute_default_source_is_byte_identical tests/test_chart_indicators.py::test_compute_remaps_multi_output_bollinger_on_ohlc4 -q
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): remap the input column to the chosen price source in PriceChart._compute"
```


### Task 33: `_IndicatorSettings` Source combo as the first Inputs row (gated) + `_accept` + `_reset_defaults`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (combo shown/hidden by gate, reads current, resets to close)**

Append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_shows_source_combo_for_selectable tests/test_chart_indicators.py::test_settings_hides_source_combo_when_not_selectable tests/test_chart_indicators.py::test_settings_source_combo_reflects_current_source tests/test_chart_indicators.py::test_settings_reset_defaults_resets_source_to_close -q
```
Expected reason: `AttributeError: '_IndicatorSettings' object has no attribute '_source_combo'` — the combo doesn't exist yet.

- [ ] **Step 3: Minimal implementation**

In `_IndicatorSettings.__init__`, match the existing Inputs-tab block (from the form creation through `addTab`):

```python
        # --- Inputs tab (one editor per registry Param) ---
        inputs = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(inputs)
        form.setContentsMargins(4, 10, 4, 4)
        form.setSpacing(9)
        self._param_widgets = {}
        for p in self._spec.params:
```

Replace with (insert the Source combo as the FIRST row, before the param loop):

```python
        # --- Inputs tab (one editor per registry Param) ---
        inputs = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(inputs)
        form.setContentsMargins(4, 10, 4, 4)
        form.setSpacing(9)
        # Source dropdown (D1/D2): the FIRST Inputs row for single-series indicators (TV places it
        # above the numeric params); None when the indicator isn't source-selectable.
        self._source_combo = None
        if is_source_selectable(self._spec):
            self._source_combo = QtWidgets.QComboBox()
            for key in _SOURCE_OPTIONS:
                self._source_combo.addItem(key, key)  # userData = source key
            cur_src = getattr(ind, "source", "close")
            idx = _SOURCE_OPTIONS.index(cur_src) if cur_src in _SOURCE_OPTIONS else _SOURCE_OPTIONS.index("close")
            self._source_combo.setCurrentIndex(idx)
            form.addRow("Source", self._source_combo)
        self._param_widgets = {}
        for p in self._spec.params:
```

In `_IndicatorSettings._accept`, match the existing body:

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

Replace with (read the source; keep the SAME 5-arg emit for now — the arity bump is Task 34):

```python
    def _accept(self):
        params = {}
        for p in self._spec.params:
            params[p.name] = self._param_widgets[p.name].value()
        colors = [b.property("color_hex") for b in self._color_btns]
        widths = [int(c.currentData()) for c in self._width_combos]
        styles = [str(c.currentData()) for c in self._style_combos]
        intervals = self._chosen_intervals()
        source = self._source_combo.currentData() if self._source_combo is not None else "close"  # noqa: F841 - emitted in Task 34
        self.applied.emit(params, colors, widths, styles, intervals)
        self.accept()
```

In `_IndicatorSettings._reset_defaults`, match the (already updated in Task 31) first line and the trailing interval loop:

```python
        params, colors, widths, styles, _source = _Indicator.spec_defaults(self._spec)
```

Replace with (capture the source instead of discarding it):

```python
        params, colors, widths, styles, source = _Indicator.spec_defaults(self._spec)
```

Then match the end of `_reset_defaults`:

```python
        for cb in self._iv_checks.values():  # default visibility = every interval
            cb.setChecked(True)
```

Replace with (reset the Source combo to the default `close` when present):

```python
        for cb in self._iv_checks.values():  # default visibility = every interval
            cb.setChecked(True)
        if self._source_combo is not None:
            si = _SOURCE_OPTIONS.index(source) if source in _SOURCE_OPTIONS else _SOURCE_OPTIONS.index("close")
            self._source_combo.setCurrentIndex(si)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_shows_source_combo_for_selectable tests/test_chart_indicators.py::test_settings_hides_source_combo_when_not_selectable tests/test_chart_indicators.py::test_settings_source_combo_reflects_current_source tests/test_chart_indicators.py::test_settings_reset_defaults_resets_source_to_close -q
```
Expected: 4 passed. (The existing `test_settings_emits_params_on_ok` still passes — the emit is still 5-arg; the source is read but not yet emitted.)

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): add gated Source combo as the first Inputs row, read in _accept, reset in _reset_defaults"
```


### Task 34: Arity bump — widen `applied`, emit `source`, update `edit_indicator` lambda + `_apply_edit`, and the `test_settings_emits_params_on_ok` lambda

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (source flows from dialog Ok into ind.source + recompute)**

Append to `tests/test_chart_indicators.py`:

```python
def test_settings_emits_source_on_ok(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    dlg._source_combo.setCurrentIndex(_SOURCE_OPTIONS_FOR_TEST.index("hl2"))
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
```

Also add this module-level constant near the top of the test file's PHASE B section (so the emit test can index into the source list without re-importing inside the lambda). Put it right after the `_valid` helper:

```python
_SOURCE_OPTIONS_FOR_TEST = ["open", "high", "low", "close", "hl2", "hlc3", "ohlc4", "hlcc4"]
```

And UPDATE the existing `test_settings_emits_params_on_ok` lambda to the new 6-arg signature. Match the current body:

```python
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals: got.update(
            params=params, colors=colors, widths=widths, styles=styles, intervals=intervals
        )
    )
    dlg._accept()
```

Replace with:

```python
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source: got.update(
            params=params, colors=colors, widths=widths, styles=styles, intervals=intervals,
            source=source,
        )
    )
    dlg._accept()
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_emits_source_on_ok tests/test_chart_indicators.py::test_apply_edit_assigns_source_and_recomputes tests/test_chart_indicators.py::test_settings_emits_params_on_ok -q
```
Expected reason: `applied` is still `Signal(dict, list, list, list, object)` and `_accept` still emits 5 args, so the 6-arg slot lambdas never receive `source` — `test_settings_emits_source_on_ok` gets a `TypeError`/missing-arg from the slot and `got['source']` is absent; the updated `test_settings_emits_params_on_ok` lambda (6 params) won't match a 5-arg emit. `_apply_edit` has no `source` kwarg, so `test_apply_edit_assigns_source_and_recomputes` raises `TypeError: _apply_edit() got an unexpected keyword argument 'source'`.

- [ ] **Step 3: Minimal implementation**

Widen the `applied` signal declaration. Match:

```python
    applied = QtCore.Signal(dict, list, list, list, object)
```

Replace with:

```python
    applied = QtCore.Signal(dict, list, list, list, object, str)
```

Update `_IndicatorSettings._accept` to emit the source. Match (the version from Task 33):

```python
        intervals = self._chosen_intervals()
        source = self._source_combo.currentData() if self._source_combo is not None else "close"  # noqa: F841 - emitted in Task 34
        self.applied.emit(params, colors, widths, styles, intervals)
        self.accept()
```

Replace with:

```python
        intervals = self._chosen_intervals()
        source = self._source_combo.currentData() if self._source_combo is not None else "close"
        self.applied.emit(params, colors, widths, styles, intervals, source)
        self.accept()
```

Update `edit_indicator`'s connect lambda. Match the existing block:

```python
        dlg.applied.connect(
            lambda params, colors, widths, styles, intervals, u=uid: self._apply_edit(
                u, params, colors, widths=widths, styles=styles, intervals=intervals
            )
        )
        dlg.exec()
```

Replace with:

```python
        dlg.applied.connect(
            lambda params, colors, widths, styles, intervals, source, u=uid: self._apply_edit(
                u, params, colors, widths=widths, styles=styles, intervals=intervals, source=source
            )
        )
        dlg.exec()
```

Update `_apply_edit`'s signature and assign `ind.source` BEFORE the `_compute` calls. Match the existing head of `_apply_edit`:

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
```

Replace with (add `source=_UNSET`; assign `ind.source` before the recompute branches so `_compute` sees the new source):

```python
    def _apply_edit(self, uid: int, params: dict, colors: list,
                    widths=_UNSET, styles=_UNSET, intervals=_UNSET, source=_UNSET):
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
        if source is not _UNSET:
            ind.source = source            # assigned BEFORE _compute so the remap uses the new source
        if ind.kind in ("oscillator", "pairs") and ind.pane is not None:
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_emits_source_on_ok tests/test_chart_indicators.py::test_apply_edit_assigns_source_and_recomputes tests/test_chart_indicators.py::test_apply_edit_default_source_unset_preserves tests/test_chart_indicators.py::test_settings_emits_params_on_ok -q
```
Expected: 4 passed. Then run the whole file to confirm the arity bump didn't regress the baseline (every `edit_indicator`/`clone`/Object-tree path now carries the 6th arg):

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected: all pass.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): bump applied signal arity to carry source; thread it through _accept/edit_indicator/_apply_edit"
```


### Task 35: `clone_indicator` carries `source`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test (clone preserves a non-default source)**

Append to `tests/test_chart_indicators.py`:

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_clone_carries_non_default_source -q
```
Expected reason: `clone_indicator` never passes `source` to `_apply_edit`, so the clone keeps the default `close` — `clone.source == 'hlc3'` fails (it's `'close'`) and the series/label assertions fail.

- [ ] **Step 3: Minimal implementation**

In `clone_indicator`, match the existing `_apply_edit` call:

```python
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

Replace with (carry the source):

```python
        clone = self.add_indicator(ind.name, params=dict(ind.params), benchmark=ind.benchmark)
        if clone is not None:
            self._apply_edit(
                clone.uid, dict(clone.params), list(ind.colors),
                widths=list(getattr(ind, "widths", clone.widths)),
                styles=list(getattr(ind, "styles", clone.styles)),
                intervals=(set(ind.intervals) if ind.intervals is not None else None),
                source=getattr(ind, "source", "close"),
            )
        return clone
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_clone_carries_non_default_source -q
```
Expected: 1 passed. Then the full file:

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```
Expected: all pass (Phase B complete).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "feat(chart): carry source through clone_indicator so a cloned indicator keeps its price source"
```

<!-- PHASE B DONE -->
```

---

## Phase C — Threshold bands

### Task 60: `_INDICATOR_BANDS` canonical threshold table

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_indicator_bands_table_canonical_values -q
```

Expected failure: `ImportError: cannot import name '_INDICATOR_BANDS' from 'vike_trader_app.ui.chart'` (the table does not exist yet).

- [ ] **Step 3: Minimal implementation**

Match the existing end of the `_OVERLAY_NAMES` region (the block that closes `_OVERLAY_NAMES` then opens `_INDICATOR_NAMES`):

```python
    # volume / statistics that ride the price scale
    "vwap", "linearreg", "linearreg_intercept", "tsf", "std_error_bands",
})

# Full descriptive names for the picker's right column (TradingView-style). Candlestick
```

Replace with (insert the `_INDICATOR_BANDS` table between `_OVERLAY_NAMES` and `_INDICATOR_NAMES`):

```python
    # volume / statistics that ride the price scale
    "vwap", "linearreg", "linearreg_intercept", "tsf", "std_error_bands",
})

# Canonical threshold guide lines per oscillator (label, value). Each value is verified against
# the indicator fn's NATIVE output range (e.g. williams_r is [-100, 0] -> -20/-80, NOT 20/80).
# `mfi` is intentionally absent (not registered). Bands seed `_Indicator.bands`; they are editable
# in the Style tab and render as dashed horizontal InfiniteLines in the oscillator pane.
_BAND_ZERO = [("Zero", 0.0)]  # the 0-centerline family (macd/ppo/mom/roc/... all cross zero)
_INDICATOR_BANDS = {
    "rsi": [("Upper", 70.0), ("Middle", 50.0), ("Lower", 30.0)],
    "stochastic": [("Upper", 80.0), ("Lower", 20.0)],
    "stochf": [("Upper", 80.0), ("Lower", 20.0)],
    "stochrsi": [("Upper", 80.0), ("Lower", 20.0)],
    "williams_r": [("Upper", -20.0), ("Lower", -80.0)],   # native [-100, 0]
    "cci": [("Upper", 100.0), ("Middle", 0.0), ("Lower", -100.0)],
    "ultosc": [("Upper", 70.0), ("Lower", 30.0)],
    "aroon": [("Upper", 70.0), ("Lower", 30.0)],
    "adx": [("Threshold", 25.0)],
    "adxr": [("Threshold", 25.0)],
    "connors_rsi": [("Upper", 90.0), ("Lower", 10.0)],
    "zscore": [("Upper", 2.0), ("Middle", 0.0), ("Lower", -2.0)],
    "spread_zscore": [("Upper", 2.0), ("Middle", 0.0), ("Lower", -2.0)],
    # 0-centerline family — a single guide line at zero
    "macd": list(_BAND_ZERO), "ppo": list(_BAND_ZERO), "apo": list(_BAND_ZERO),
    "mom": list(_BAND_ZERO), "roc": list(_BAND_ZERO), "rocp": list(_BAND_ZERO),
    "ao": list(_BAND_ZERO), "ac": list(_BAND_ZERO), "dpo": list(_BAND_ZERO),
    "trix": list(_BAND_ZERO), "tsi": list(_BAND_ZERO), "smi_ergodic": list(_BAND_ZERO),
    "cmo": list(_BAND_ZERO), "elder_ray": list(_BAND_ZERO), "kvo": list(_BAND_ZERO),
    "adosc": list(_BAND_ZERO), "net_volume": list(_BAND_ZERO), "bop": list(_BAND_ZERO),
}

# Full descriptive names for the picker's right column (TradingView-style). Candlestick
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_indicator_bands_table_canonical_values -q
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): canonical _INDICATOR_BANDS threshold table (Phase C)

Range-verified guide-line values per oscillator (williams_r -20/-80,
0-centerline family, etc.); mfi excluded (not registered).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 61: `_Indicator.bands` seed + band colours + `band_defaults`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_indicator_bands_seed_and_colors -q
```

Expected failure: `AttributeError: '_Indicator' object has no attribute 'bands'` (no `bands`/`band_colors`/`band_defaults` yet).

- [ ] **Step 3: Minimal implementation**

Match the current `_Indicator.__init__` tail (post-Phase-B, which added `self.source = "close"`). The current code reads:

```python
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.widths = [1] * max(1, len(spec.outputs))    # per-output line width (px)
        self.styles = ["solid"] * max(1, len(spec.outputs))  # per-output line style name
        self.source = "close"            # price source feeding a single-series indicator
        self.series = {}                 # computed: output label -> full series
```

Replace with (add `bands` + `band_colors` after `source`, only for oscillator/pairs kinds):

```python
        self.colors = list(_OVERLAY_COLORS[: max(1, len(spec.outputs))])  # per-output colour
        self.widths = [1] * max(1, len(spec.outputs))    # per-output line width (px)
        self.styles = ["solid"] * max(1, len(spec.outputs))  # per-output line style name
        self.source = "close"            # price source feeding a single-series indicator
        # Threshold guide lines (oscillator/pairs only): mutable per-instance [label, value] pairs
        # seeded from _INDICATOR_BANDS, + a per-band colour (default dim theme.TEXT3). Kept OUT of
        # _curves so they never pollute reveal's autoscale or the crosshair value-at-x scan.
        seed = _INDICATOR_BANDS.get(name, []) if kind in ("oscillator", "pairs") else []
        self.bands = [[lbl, float(val)] for lbl, val in seed]     # editable copies
        self.band_colors = [theme.TEXT3 for _ in seed]            # per-band colour
        self.series = {}                 # computed: output label -> full series
```

> Note: if Phase B did not add the trailing `self.source = "close"` comment exactly as above, match the real `self.source = "close"` line and insert the `seed`/`self.bands`/`self.band_colors` block immediately after it, before `self.series = {}`.

Then add the `band_defaults` static method. Match the existing `spec_defaults` static method (post-Phase-B it returns 4-or-5 tuples; do NOT alter it) and add `band_defaults` right after it. The current `spec_defaults` ends:

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
```

Replace with (insert `band_defaults` between `spec_defaults` and `label`):

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

    @staticmethod
    def band_defaults(name):
        """Canonical (label, value) threshold seed for the Defaults button — a fresh copy of
        the _INDICATOR_BANDS row (empty for overlays / unlisted indicators)."""
        return [(lbl, float(val)) for lbl, val in _INDICATOR_BANDS.get(name, [])]

    @property
    def label(self) -> str:
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_indicator_bands_seed_and_colors -q
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): seed _Indicator.bands + per-band colours + band_defaults (Phase C)

Per-instance mutable [label, value] copies for oscillator/pairs; default
dim theme.TEXT3 colour; band_defaults() exposes the canonical seed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 62: `OscillatorPane` builds dashed band lines in `_band_lines`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_builds_band_lines_out_of_curves tests/test_chart_indicators.py::test_oscillator_macd_single_zero_band -q
```

Expected failure: `AttributeError: 'OscillatorPane' object has no attribute '_band_lines'`.

- [ ] **Step 3: Minimal implementation**

First add the `_band_lines` dict next to `_curves` in `OscillatorPane.__init__`. Match:

```python
        self._inds = []           # list[_Indicator] hosted in this pane
        self._curves = {}         # uid -> {output label: PlotDataItem}
        self._rows = {}           # uid -> _LegendRow
```

Replace with:

```python
        self._inds = []           # list[_Indicator] hosted in this pane
        self._curves = {}         # uid -> {output label: PlotDataItem}
        self._band_lines = {}     # uid -> [InfiniteLine] threshold guides (kept OUT of _curves)
        self._rows = {}           # uid -> _LegendRow
```

Add a `_build_bands` helper + call it from `_build_curves`. Match the current `_build_curves`:

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

Replace with (add the `_build_bands` call + the new method):

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
        self._build_bands(ind)

    def _build_bands(self, ind: "_Indicator"):
        """Dashed horizontal threshold guides (RSI 70/30, MACD 0, …). Stored in `_band_lines`,
        NEVER in `_curves`, so they don't pollute reveal's autoscale, `set_value`, or the crosshair
        value-at-x scan. `ignoreBounds=True` keeps them from forcing the pane's y-range (reveal
        unions them explicitly). Removed + rebuilt alongside curves in remove_ind / update_ind."""
        lines = []
        bands = getattr(ind, "bands", [])
        colors = getattr(ind, "band_colors", [])
        for i, (_lbl, val) in enumerate(bands):
            col = colors[i] if i < len(colors) else theme.TEXT3
            pen = pg.mkPen(col, width=1, style=QtCore.Qt.DashLine)
            ln = pg.InfiniteLine(angle=0, pos=float(val), movable=False, pen=pen)
            self.addItem(ln, ignoreBounds=True)
            lines.append(ln)
        self._band_lines[ind.uid] = lines
```

Update `remove_ind` to drop band lines. Match:

```python
    def remove_ind(self, uid: int) -> int:
        """Remove one indicator; returns the number of indicators left in the pane."""
        for c in self._curves.pop(uid, {}).values():
            self.removeItem(c)
        row = self._rows.pop(uid, None)
```

Replace with:

```python
    def remove_ind(self, uid: int) -> int:
        """Remove one indicator; returns the number of indicators left in the pane."""
        for c in self._curves.pop(uid, {}).values():
            self.removeItem(c)
        for ln in self._band_lines.pop(uid, []):
            self.removeItem(ln)
        row = self._rows.pop(uid, None)
```

Update `update_ind` to remove + rebuild band lines (the rebuild happens inside `_build_curves` → `_build_bands`). Match:

```python
    def update_ind(self, ind: "_Indicator"):
        """After an edit: rebuild that indicator's curves + refresh its legend row."""
        for c in self._curves.get(ind.uid, {}).values():
            self.removeItem(c)
        self._build_curves(ind)
        if ind.uid in self._rows:
            self._rows[ind.uid].refresh(ind)
```

Replace with:

```python
    def update_ind(self, ind: "_Indicator"):
        """After an edit: rebuild that indicator's curves + band lines + refresh its legend row."""
        for c in self._curves.get(ind.uid, {}).values():
            self.removeItem(c)
        for ln in self._band_lines.get(ind.uid, []):  # drop old guides before _build_bands re-adds
            self.removeItem(ln)
        self._build_curves(ind)
        if ind.uid in self._rows:
            self._rows[ind.uid].refresh(ind)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_oscillator_builds_band_lines_out_of_curves tests/test_chart_indicators.py::test_oscillator_macd_single_zero_band -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): render dashed band lines in OscillatorPane._band_lines (Phase C)

Threshold guides as angle-0 InfiniteLines (ignoreBounds), kept out of
_curves; removed+rebuilt in remove_ind/update_ind like the curves.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 63: `reveal` unions band values into the pane y-range (extend-only)

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**

```python
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
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_reveal_unions_band_values_extend_only tests/test_chart_indicators.py::test_reveal_band_below_series_extends_low -q
```

Expected failure: for `rsi`/`williams_r` the band values fall outside the data-only autoscale range — e.g. williams_r data is bounded by its series minimum (often > -80), so `lo <= -80.0` is False; the rsi assertion can also fail when 70/30 lie outside the tight visible-series band.

- [ ] **Step 3: Minimal implementation**

Match the current `OscillatorPane.reveal`:

```python
    def reveal(self, index: int):
        all_ys = []
        for ind in self._inds:
            last = None
            for label, curve in self._curves.get(ind.uid, {}).items():
                series = ind.series.get(label, [])
                xs = [k for k in range(min(index + 1, len(series))) if series[k] is not None]
                ys = [series[k] for k in xs]
                curve.setData(xs, ys)
                curve.setVisible(ind.shown)
                if ind.shown:
                    all_ys += ys
                if ys:
                    last = ys[-1]
            if ind.uid in self._rows:
                self._rows[ind.uid].set_value(f"{last:,.2f}" if last is not None else "")
        if all_ys:
            lo, hi = min(all_ys), max(all_ys)
            if hi > lo:
                self.setYRange(lo, hi, padding=0.12)
```

Replace with (union the shown indicators' band values into `all_ys`, extend-only):

```python
    def reveal(self, index: int):
        all_ys = []
        for ind in self._inds:
            last = None
            for label, curve in self._curves.get(ind.uid, {}).items():
                series = ind.series.get(label, [])
                xs = [k for k in range(min(index + 1, len(series))) if series[k] is not None]
                ys = [series[k] for k in xs]
                curve.setData(xs, ys)
                curve.setVisible(ind.shown)
                if ind.shown:
                    all_ys += ys
                if ys:
                    last = ys[-1]
            # union the threshold guide values (extend-only) so the dashed lines stay on-screen;
            # band lines live in _band_lines (ignoreBounds), so they never autoscale on their own.
            if ind.shown:
                all_ys += [float(val) for _lbl, val in getattr(ind, "bands", [])]
            if ind.uid in self._rows:
                self._rows[ind.uid].set_value(f"{last:,.2f}" if last is not None else "")
        if all_ys:
            lo, hi = min(all_ys), max(all_ys)
            if hi > lo:
                self.setYRange(lo, hi, padding=0.12)
```

- [ ] **Step 4: Run-to-pass (plus the lockstep guard)**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_reveal_unions_band_values_extend_only tests/test_chart_indicators.py::test_reveal_band_below_series_extends_low tests/test_chart_indicators.py::test_oscillator_reveals_in_lockstep -q
```

Expected: 3 passed (the band union counts curve points via `_curves` only, so `test_oscillator_reveals_in_lockstep`'s point count is unaffected).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): union band values into oscillator reveal y-range (Phase C)

Extend-only — shown indicators' threshold values widen lo/hi so dashed
guides stay visible; data range is never overridden. Lockstep guard intact.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 64: Settings Style tab — per-band value spin + colour button

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**

```python
def test_settings_builds_band_rows(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")           # 3 bands
    dlg = _IndicatorSettings(ind)
    assert len(dlg._band_value_spins) == 3
    assert len(dlg._band_color_btns) == 3
    assert [s.value() for s in dlg._band_value_spins] == [70.0, 50.0, 30.0]
    # colour buttons carry the seeded per-band colour
    from vike_trader_app.ui import theme
    assert all(b.property("color_hex") == theme.TEXT3 for b in dlg._band_color_btns)


def test_settings_no_band_rows_for_overlay(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("ema")           # overlay -> no bands
    dlg = _IndicatorSettings(ind)
    assert dlg._band_value_spins == [] and dlg._band_color_btns == []


def test_settings_reset_defaults_repopulates_bands(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    dlg._band_value_spins[0].setValue(88.0)   # edit Upper
    dlg._reset_defaults()
    assert [s.value() for s in dlg._band_value_spins] == [70.0, 50.0, 30.0]
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_builds_band_rows tests/test_chart_indicators.py::test_settings_no_band_rows_for_overlay tests/test_chart_indicators.py::test_settings_reset_defaults_repopulates_bands -q
```

Expected failure: `AttributeError: '_IndicatorSettings' object has no attribute '_band_value_spins'`.

- [ ] **Step 3: Minimal implementation**

Add the per-band rows at the end of the Style tab, right before `tabs.addTab(style, "Style")`. Match the current tail of the Style-tab build:

```python
            if is_pattern:  # markers use brushes, not pens -> no width/style
                wcb.hide()
                scb.hide()
            sform.addRow(out.replace("_", " ").title(), roww)
        tabs.addTab(style, "Style")
```

Replace with (append a band value-spin + colour-button row per band):

```python
            if is_pattern:  # markers use brushes, not pens -> no width/style
                wcb.hide()
                scb.hide()
            sform.addRow(out.replace("_", " ").title(), roww)

        # --- per-band threshold rows (oscillator/pairs only): value spin + colour button ---
        self._band_value_spins = []
        self._band_color_btns = []
        bands = getattr(ind, "bands", [])
        band_colors = getattr(ind, "band_colors", [])
        for i, (blbl, bval) in enumerate(bands):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setDecimals(2)
            spin.setRange(-1e9, 1e9)
            spin.setSingleStep(1.0)
            spin.setValue(float(bval))
            self._band_value_spins.append(spin)

            cbtn = QtWidgets.QPushButton()
            cbtn.setFixedSize(46, 22)
            ccol = band_colors[i] if i < len(band_colors) else theme.TEXT3
            self._set_btn_color(cbtn, ccol)
            cbtn.clicked.connect(lambda _c=False, b=cbtn: self._pick_color(b))
            self._band_color_btns.append(cbtn)

            brow = QtWidgets.QWidget()
            browl = QtWidgets.QHBoxLayout(brow)
            browl.setContentsMargins(0, 0, 0, 0)
            browl.setSpacing(6)
            browl.addWidget(spin)
            browl.addWidget(cbtn)
            sform.addRow(f"{_indicator_code(ind.name)} {blbl} Band", brow)
        tabs.addTab(style, "Style")
```

Repopulate the band spins from the canonical seed in `_reset_defaults`. Match the current tail of `_reset_defaults`:

```python
        names = [nm for _lbl, nm in _LINE_STYLES]
        for i, cb in enumerate(self._style_combos):
            nm = styles[i % len(styles)]
            cb.setCurrentIndex(names.index(nm) if nm in names else 0)
        for cb in self._iv_checks.values():  # default visibility = every interval
            cb.setChecked(True)
```

Replace with (reset band spins + band colours to the canonical seed / dim default):

```python
        names = [nm for _lbl, nm in _LINE_STYLES]
        for i, cb in enumerate(self._style_combos):
            nm = styles[i % len(styles)]
            cb.setCurrentIndex(names.index(nm) if nm in names else 0)
        band_seed = _Indicator.band_defaults(self._ind.name)  # canonical (label, value) pairs
        for i, spin in enumerate(self._band_value_spins):
            if i < len(band_seed):
                spin.setValue(float(band_seed[i][1]))
        for btn in self._band_color_btns:                     # default dim guide colour
            self._set_btn_color(btn, theme.TEXT3)
        for cb in self._iv_checks.values():  # default visibility = every interval
            cb.setChecked(True)
```

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_builds_band_rows tests/test_chart_indicators.py::test_settings_no_band_rows_for_overlay tests/test_chart_indicators.py::test_settings_reset_defaults_repopulates_bands -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): Style-tab per-band value spin + colour button (Phase C)

Editable threshold value + colour per band when ind.bands is non-empty;
Defaults resets band values to the canonical seed and colours to dim TEXT3.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 65: Widen `applied`, thread `bands` through `_accept` / `edit_indicator` / `_apply_edit`

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

> Phase B already widened `applied` to `Signal(dict, list, list, list, object, str)`, `_accept` emits 6 args (`…, source`), `edit_indicator` connects a 6-arg lambda, `_apply_edit` has `source=_UNSET`, and `clone_indicator` passes `source`. This task performs the SECOND arity bump (adds `bands`).

- [ ] **Step 1: Write failing test (+ update the existing emit test's lambda)**

First UPDATE the existing `test_settings_emits_params_on_ok` to add the new `bands` arg to its connect lambda (its Phase-B form takes `source`). Match the post-Phase-B test:

```python
def test_settings_emits_params_on_ok(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    p0 = ind.spec.params[0]
    dlg._param_widgets[p0.name].setValue(9)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source: got.update(
            params=params, colors=colors, widths=widths, styles=styles,
            intervals=intervals, source=source
        )
    )
    dlg._accept()
    assert got["params"][p0.name] == 9
    assert len(got["colors"]) == len(ind.spec.outputs)
    assert len(got["widths"]) == len(ind.spec.outputs)
    assert len(got["styles"]) == len(ind.spec.outputs)
```

Replace with (add `bands` to the lambda + assert the payload shape):

```python
def test_settings_emits_params_on_ok(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    dlg = _IndicatorSettings(ind)
    p0 = ind.spec.params[0]
    dlg._param_widgets[p0.name].setValue(9)
    got = {}
    dlg.applied.connect(
        lambda params, colors, widths, styles, intervals, source, bands: got.update(
            params=params, colors=colors, widths=widths, styles=styles,
            intervals=intervals, source=source, bands=bands
        )
    )
    dlg._accept()
    assert got["params"][p0.name] == 9
    assert len(got["colors"]) == len(ind.spec.outputs)
    assert len(got["widths"]) == len(ind.spec.outputs)
    assert len(got["styles"]) == len(ind.spec.outputs)
    # bands payload: [(label, value, color), …] for the 3 rsi guides
    assert [(lbl, val) for lbl, val, _c in got["bands"]] == [("Upper", 70.0), ("Middle", 50.0), ("Lower", 30.0)]
    assert all(isinstance(c, str) for _l, _v, c in got["bands"])
```

Then add a round-trip test that an edited band value updates `ind.bands` AND the rendered line:

```python
def test_band_edit_round_trip_updates_line(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    # edit Upper 70 -> 80 via _apply_edit (same path the settings dialog drives)
    new_bands = [("Upper", 80.0, "#777777"), ("Middle", 50.0, "#777777"), ("Lower", 30.0, "#777777")]
    pc._apply_edit(ind.uid, dict(ind.params), list(ind.colors), bands=new_bands)
    ind2 = pc._indicators[ind.uid]
    assert [v for _l, v in ind2.bands] == [80.0, 50.0, 30.0]
    assert ind2.band_colors == ["#777777", "#777777", "#777777"]
    # the rendered InfiniteLine moved to 80 (update_ind rebuilt the band lines)
    lines = ind2.pane._band_lines[ind2.uid]
    assert [ln.value() for ln in lines] == [80.0, 50.0, 30.0]
    # bands left UNSET on an unrelated edit must NOT change the bands
    pc._apply_edit(ind2.uid, dict(ind2.params), list(ind2.colors))
    assert [v for _l, v in pc._indicators[ind2.uid].bands] == [80.0, 50.0, 30.0]
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_emits_params_on_ok tests/test_chart_indicators.py::test_band_edit_round_trip_updates_line -q
```

Expected failure: `test_settings_emits_params_on_ok` — the 7-arg lambda mismatches the 6-arg signal (`applied.emit` sends 6 args; Qt raises a connect/argument-count error or `got["bands"]` is missing). `test_band_edit_round_trip_updates_line` — `_apply_edit() got an unexpected keyword argument 'bands'`.

- [ ] **Step 3: Minimal implementation**

Widen the `applied` signal. Match the post-Phase-B declaration:

```python
    applied = QtCore.Signal(dict, list, list, list, object, str)
```

Replace with:

```python
    applied = QtCore.Signal(dict, list, list, list, object, str, list)
```

Update `_accept` to collect + emit the bands payload. Match the post-Phase-B `_accept`:

```python
    def _accept(self):
        params = {}
        for p in self._spec.params:
            params[p.name] = self._param_widgets[p.name].value()
        colors = [b.property("color_hex") for b in self._color_btns]
        widths = [int(c.currentData()) for c in self._width_combos]
        styles = [str(c.currentData()) for c in self._style_combos]
        intervals = self._chosen_intervals()
        source = self._source_combo.currentData() if self._source_combo is not None else "close"
        self.applied.emit(params, colors, widths, styles, intervals, source)
        self.accept()
```

Replace with (build `[(label, value, color), …]` from the band rows + emit it):

```python
    def _accept(self):
        params = {}
        for p in self._spec.params:
            params[p.name] = self._param_widgets[p.name].value()
        colors = [b.property("color_hex") for b in self._color_btns]
        widths = [int(c.currentData()) for c in self._width_combos]
        styles = [str(c.currentData()) for c in self._style_combos]
        intervals = self._chosen_intervals()
        source = self._source_combo.currentData() if self._source_combo is not None else "close"
        bands = [
            (self._ind.bands[i][0], float(spin.value()), btn.property("color_hex"))
            for i, (spin, btn) in enumerate(zip(self._band_value_spins, self._band_color_btns))
        ]
        self.applied.emit(params, colors, widths, styles, intervals, source, bands)
        self.accept()
```

> The band LABEL is preserved from `self._ind.bands[i][0]` (the Style tab edits values/colours only, never labels), so a (label, value, colour) triple round-trips cleanly.

Update the `edit_indicator` connect lambda. Match the post-Phase-B body:

```python
    def edit_indicator(self, uid: int):
        """Open the Settings dialog (Inputs + Style); apply -> recompute + re-render."""
        ind = self._indicators.get(uid)
        if ind is None:
            return
        dlg = _IndicatorSettings(ind, self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.applied.connect(
            lambda params, colors, widths, styles, intervals, source, u=uid: self._apply_edit(
                u, params, colors, widths=widths, styles=styles, intervals=intervals, source=source
            )
        )
        dlg.exec()
```

Replace with (add `bands` to the lambda + pass it through):

```python
    def edit_indicator(self, uid: int):
        """Open the Settings dialog (Inputs + Style); apply -> recompute + re-render."""
        ind = self._indicators.get(uid)
        if ind is None:
            return
        dlg = _IndicatorSettings(ind, self)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        dlg.applied.connect(
            lambda params, colors, widths, styles, intervals, source, bands, u=uid: self._apply_edit(
                u, params, colors, widths=widths, styles=styles, intervals=intervals,
                source=source, bands=bands
            )
        )
        dlg.exec()
```

Update `_apply_edit` to accept + apply `bands`. Match the post-Phase-B signature/body head:

```python
    def _apply_edit(self, uid: int, params: dict, colors: list,
                    widths=_UNSET, styles=_UNSET, intervals=_UNSET, source=_UNSET):
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
        if source is not _UNSET:
            ind.source = source
        if ind.kind in ("oscillator", "pairs") and ind.pane is not None:
            self._compute(ind)
            ind.pane.update_ind(ind)
            ind.pane.reveal(self._reveal_index())
```

Replace with (add the `bands` param + split the payload into `ind.bands` / `ind.band_colors` before the recompute/update branch):

```python
    def _apply_edit(self, uid: int, params: dict, colors: list,
                    widths=_UNSET, styles=_UNSET, intervals=_UNSET, source=_UNSET, bands=_UNSET):
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
        if source is not _UNSET:
            ind.source = source
        if bands is not _UNSET:  # payload is [(label, value, color), …] -> split into the two lists
            ind.bands = [[lbl, float(val)] for lbl, val, _c in bands]
            ind.band_colors = [c for _l, _v, c in bands]
        if ind.kind in ("oscillator", "pairs") and ind.pane is not None:
            self._compute(ind)
            ind.pane.update_ind(ind)   # rebuilds curves + band lines from the new ind.bands
            ind.pane.reveal(self._reveal_index())
```

> The rest of `_apply_edit` (the `else` overlay branch, `_sync_shown` / `_apply_visibility` / `_reveal_indicator` / `_refresh_legends` tail) is unchanged.

- [ ] **Step 4: Run-to-pass**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_settings_emits_params_on_ok tests/test_chart_indicators.py::test_band_edit_round_trip_updates_line -q
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): thread editable bands through applied/_apply_edit (Phase C)

Second arity bump: applied gains a list of (label, value, colour); _accept
emits it, edit_indicator forwards it, _apply_edit(bands=_UNSET) splits it
into ind.bands/ind.band_colors and update_ind rebuilds the guide lines.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 66: `clone_indicator` carries bands (+ band colours)

**Files**
- Modify: `src/vike_trader_app/ui/chart.py`
- Test: `tests/test_chart_indicators.py`

- [ ] **Step 1: Write failing test**

```python
def test_clone_carries_edited_bands(app):
    pc, _ = _chart(app)
    ind = pc.add_indicator("rsi")
    # edit the original's bands first (70 -> 80, custom colour)
    pc._apply_edit(
        ind.uid, dict(ind.params), list(ind.colors),
        bands=[("Upper", 80.0, "#abcdef"), ("Middle", 50.0, "#abcdef"), ("Lower", 30.0, "#abcdef")],
    )
    clone = pc.clone_indicator(ind.uid)
    assert clone is not None and clone.uid != ind.uid
    assert [v for _l, v in clone.bands] == [80.0, 50.0, 30.0]
    assert clone.band_colors == ["#abcdef", "#abcdef", "#abcdef"]
    # deep copy — editing the clone's bands must not mutate the original
    clone.bands[0][1] = 99.0
    assert pc._indicators[ind.uid].bands[0][1] == 80.0
    # the clone's rendered band line reflects the carried value
    assert [ln.value() for ln in clone.pane._band_lines[clone.uid]] == [80.0, 50.0, 30.0]
```

- [ ] **Step 2: Run-to-fail**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_clone_carries_edited_bands -q
```

Expected failure: the clone keeps its freshly-seeded canonical bands (Upper == 70.0), so `[v for _l, v in clone.bands] == [80.0, 50.0, 30.0]` is False — `clone_indicator` does not pass `bands` yet.

- [ ] **Step 3: Minimal implementation**

Match the post-Phase-B `clone_indicator` (it already carries `source`):

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
                source=getattr(ind, "source", "close"),
            )
        return clone
```

Replace with (build a `(label, value, colour)` payload from the source indicator's bands and pass it):

```python
    def clone_indicator(self, uid: int):
        """Duplicate an indicator (same params/colours/width/style/intervals/source/bands)
        — TradingView's 'Clone'."""
        ind = self._indicators.get(uid)
        if ind is None:
            return None
        clone = self.add_indicator(ind.name, params=dict(ind.params), benchmark=ind.benchmark)
        if clone is not None:
            src_bands = getattr(ind, "bands", [])
            src_band_colors = getattr(ind, "band_colors", [])
            bands_payload = [
                (lbl, float(val),
                 src_band_colors[i] if i < len(src_band_colors) else theme.TEXT3)
                for i, (lbl, val) in enumerate(src_bands)
            ]
            self._apply_edit(
                clone.uid, dict(clone.params), list(ind.colors),
                widths=list(getattr(ind, "widths", clone.widths)),
                styles=list(getattr(ind, "styles", clone.styles)),
                intervals=(set(ind.intervals) if ind.intervals is not None else None),
                source=getattr(ind, "source", "close"),
                bands=bands_payload,
            )
        return clone
```

- [ ] **Step 4: Run-to-pass (+ full-file regression)**

```
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py::test_clone_carries_edited_bands -q
PYTHONPATH="$(pwd)/src" "C:/Projects/vike-trader-app/.venv/Scripts/python.exe" -m pytest tests/test_chart_indicators.py -q
```

Expected: `test_clone_carries_edited_bands` passes; the full file is green (109 baseline + Phase A + Phase B + the new Phase C tests).

- [ ] **Step 5: Commit**

```
git add src/vike_trader_app/ui/chart.py tests/test_chart_indicators.py
git commit -m "$(cat <<'EOF'
feat(chart): clone_indicator carries edited bands + colours (Phase C)

Clone passes a deep (label, value, colour) payload so a customised
threshold set survives duplication, alongside source/params/style.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

<!-- PHASE C DONE -->
```
