# Chart indicators → TradingView / TradeLocker parity

**Status:** Approved design — ready for implementation plan
**Date:** 2026-06-04
**Branch / worktree:** `worktree-feat+chart-indicators-tv-parity`
**Touches:** `src/vike_trader_app/ui/chart.py` only (no `app.py`, no registry change for CORE scope)
**Tests:** extend `tests/test_chart_indicators.py` (37 green baseline)

---

## 1. Goal

Bring on-chart indicators to TradingView / TradeLocker parity in three areas the user named, **in this order**:

1. **Time alignment** (the real correctness bug) — oscillator panes must line up in time with the candles, and the time axis must sit at the very bottom under the lowest pane.
2. **Moving among panes (up/down)** — a TradingView-style per-pane hover toolbar (move up / move down / maximize / delete).
3. **Indicator settings dialog** — deepen the settings to TV's Inputs / Style / Visibility model.

TradeLocker's chart **is** the TradingView Charting Library, so its behavior is identical to the TradingView chart. Behavior below was captured live (Playwright on tradingview.com: added RSI → studied the pane, hover toolbar, right-click menu, and all three settings tabs).

### Reference behavior captured live
- **Time align:** the time axis sits at the **very bottom under the lowest pane** (not under the candles). All panes' right price scales are the **same width**, so plot columns are pixel-aligned in time.
- **Pane toolbar:** hovering a pane shows, top-right, `[move up] [move down (only when a pane is below)] [maximize/restore] [delete pane]`. Right-click → *Move to*: New pane above/below, Existing pane above/below. Legend hover shows eye / settings / source / trash / more.
- **Settings dialog:** 3 tabs + a `Defaults ▾ / Cancel / Ok` footer.
  - *Inputs:* grouped sections (Length, **Source dropdown**, Calculate Divergence; Smoothing: Type/Length/BB StdDev; Calculation: Timeframe/Wait for close).
  - *Style:* per-plot row = visibility checkbox + colour(opacity) + **line width** + **line style** (solid/dashed/dotted); threshold bands (Upper 70 / Middle 50 / Lower 30) editable; gradient fills; Output values (Precision, Labels on price scale, Values in status line).
  - *Visibility:* per-timeframe-range checkboxes (Ticks/Seconds/Minutes/Hours/Days/Weeks/Months, min–max).

---

## 2. Current architecture (relevant pieces of `chart.py`)

- `TimeAxis` (l.88) / `PriceAxis` (l.128) — custom pyqtgraph axes; `TimeAxis.set_bars()` drives tick strings from bars.
- `_Indicator` (l.400) — one active indicator: `params, kind, visible, intervals, shown, own_scale, colors, series, curves, pane, scatter`.
- `_IndicatorSettings` (l.433) — popup with **Inputs** (spinboxes) + **Style** (one colour button per output); `_accept` emits `applied(params, colors)`.
- `_DragGrip` (l.571) — drag handle for pane reorder.
- `_LegendRow` (l.602) / `_PaneLegend` (l.703) / `_ObjectTree` (l.746) — legend rows + the `⋯` context menu (`_open_menu` l.663).
- `OscillatorPane` (l.817) — a `pg.PlotWidget`; right `PriceAxis`, `hideAxis("left")`, `hideAxis("bottom")`, `setXLink(price ViewBox)`; header (grip + stacked legend rows) at top-left.
- `PriceChart` (l.949) — the main `pg.PlotWidget`; time axis on bottom; `_pane_host` (the vertical `QSplitter`), `_vb2` (secondary ViewBox for own-scale overlays). Pane methods: `_new_pane` (1331), `_drag_pane` (1343), `_render` (1369), `_unrender` (1396), `move_indicator` (1560), `_reorder_pane` (1585), `_merge_into_adjacent` (1595), `_osc_panes` (1611), `_resize_panes` (1691), `show_upto` (1718), `resizeEvent` (1933).
- `app.py` l.319 / l.360 — the `QSplitter(Vertical)` with the price chart at index 0 and oscillator panes added at index ≥1. **No `app.py` change needed.** Both `self.price` and `self.studio_price` inherit all behavior from the class.
- `core/indicators/base.py` — registry; `Param(type ∈ {"int","float"})`, fixed `inputs` per indicator. **CORE scope does not touch this.**

---

## 3. Phase 1 — Time alignment

Two independent sub-fixes, both inside `chart.py`.

### 3a. Shared right-axis width across all panes

**Problem.** Each pane sizes its right axis to its own label width (price `74,600.00` vs RSI `70.00`), so the plot columns have different left/right edges → the same timestamp maps to a different screen-x in each pane → vertical misalignment. x-link already guarantees the same x **range**; the only residual variable is **axis width**.

**Correctness blocker (from adversarial pyqtgraph verification).** Do **not** compute the target width from `getAxis("right").width()`. In pyqtgraph 0.14.0, `AxisItem.width()` returns `geometry().width()` from the **last layout pass**, and `setWidth(None)`/`_updateWidth()` only set min/max width + invalidate — geometry is re-applied **lazily** on the next layout. So reading `width()` right after `setWidth(None)` returns a stale (or zero, before first paint) value, and the equalize **silently fails on the first add** — defeating the whole feature.

**Technique (synchronous, paint-independent, headless-safe).**
```
def _sync_axis_width(self):
    if self._wsyncing: return
    self._wsyncing = True
    try:
        axes = [self.getAxis("right")] + [p.getAxis("right")
                for p in self._panes_in_visual_order()]
        # natural width WITHOUT depending on a pending paint:
        # QFontMetrics(tickFont).horizontalAdvance(longest current tick string)
        #   + style['tickTextOffset'][0] + max(0, style['tickLength'])
        w = max(self._axis_natural_width(a) for a in axes)
        for a in axes:
            a.setWidth(w)
        self.getPlotItem().layout.activate()
        for p in self._panes_in_visual_order():
            p.getPlotItem().layout.activate()
    finally:
        self._wsyncing = False
```
- `_axis_natural_width(axis)` derives width from `QFontMetrics` over the axis's **current tick strings** + the axis's `tickTextOffset`/`tickLength` — fully synchronous, so headless tests can assert equality immediately.
- Pin the **price** axis too (not just the panes). When the **last** pane is removed, call `self.getAxis("right").setWidth(None)` to restore auto so a lone chart isn't stuck at a stale pinned width.
- `_wsyncing` re-entrancy guard (mirrors the existing `_fitting` guard at l.984) breaks the `setWidth → geometry → resize → sigResized → re-sync` loop.

### 3b. Bottom time axis only on the lowest pane (approach **B1**)

**Why B1 (verified).** Each pane is its own `PlotWidget`/`PlotItem` with its own scene; pyqtgraph forbids one `AxisItem` in two PlotItems, and a free-floating shared axis widget would not participate in any pane's layout (you'd hand-sync it pixel-by-pixel). With B1, the lowest pane's own bottom `TimeAxis` is laid out in the **same grid column** as that pane's ViewBox → x-identical by construction, and the pane is already x-linked to price.

**Changes.**
- `OscillatorPane.__init__`: add a bottom `TimeAxis` to `axisItems`, styled exactly like the price chart's bottom axis (l.965–975). Keep `hideAxis("bottom")` at init. Store `self._time_axis`. Update the now-stale comment ("time axis lives on the price chart").
- New `OscillatorPane.set_bars(bars)` → `self._time_axis.set_bars(bars)`.
- New `OscillatorPane.set_bottom_axis_visible(on)` → `showAxis/hideAxis("bottom")`.
- New `PriceChart._reassign_bottom_axis()`:
  - `panes = self._panes_in_visual_order()`.
  - If empty: `self.showAxis("bottom")` and `self._time_axis.set_bars(self._bars)`.
  - Else: `self.hideAxis("bottom")`; for every pane `set_bottom_axis_visible(False)` + `set_bars(self._bars)`; then `set_bottom_axis_visible(True)` on the **last** (highest splitter index) pane.
  - After toggling the price bottom axis, **explicitly** `self.getPlotItem().layout.activate()` then `self._sync_vb2()` (+ `_autorange_vb2()`) — do **not** rely on `hideAxis` emitting `sigResized` (it's lazy; see 3d).

### 3c. Orchestrator + call sites

```
def _align_panes(self):
    self._reassign_bottom_axis()   # FIRST — changes axis visibility/natural widths
    self._sync_axis_width()        # THEN — equalize across the now-correct axes
```
Idempotent; safe with zero panes. **Call `_align_panes()` from every layout/lifecycle event:**
`_new_pane`, `_unrender` (pane-drop branch, **after `setParent(None)`**, not relying on `deleteLater` timing), `_reorder_pane`, `_drag_pane` (both branches), `set_data`, `apply_live`, `set_timeframe`, `resizeEvent` (**before** l.1938 reads `getAxis("right").width()` for the top bar), and — per the completeness critic — the **tails of `move_indicator`, `_merge_into_adjacent`, and `_render`'s oscillator branch** (the merge/move paths `_unrender`+`_render` and would otherwise finalize at an intermediate state).

Bars feed: in `set_data` (after l.1182) and `apply_live` (after l.1237), loop `self._osc_panes()` and `pane.set_bars(bars)`. `_new_pane` calls `pane.set_bars(self._bars)` on creation so a fresh pane's axis isn't blank.

### 3d. Interactions to get right
- **Own-scale overlays (`_vb2`).** Hiding the price bottom axis grows the price ViewBox; `_vb2` must re-sync or own-scale overlays (Pin to scale → Own scale) misalign vertically. Fixed by the explicit `_sync_vb2()` call in `_reassign_bottom_axis` (3b).
- **Crosshair time tag.** `_cx_time_tag` is drawn on the price chart at `height()-tag_height` (l.1927–8). Once the time axis moves to the lowest pane, that tag is **orphaned** (no axis beneath it). **Phase-1 fix:** hide the price-chart time tag when panes exist (no orphan). The full cross-pane crosshair + bottom-pane time tag is **Phase 4** (§6).
- **Pane min-height.** Add `OscillatorPane.setMinimumHeight(~64px)` so the splitter enforces a floor independent of `_resize_panes` (which Phase 2 disables while maximized).
- **Even plot heights.** `_resize_panes` should add the bottom-axis strip (~20px) to the lowest pane's allotment so its plot area matches siblings (cosmetic; does not affect x-alignment).
- **Shared helper.** Add `PriceChart._panes_in_visual_order()` = `[host.widget(i) for i in range(1, host.count()) if isinstance(host.widget(i), OscillatorPane)]`. Use it **everywhere order matters**. Never use `_osc_panes()` (dict-insertion order) for ordering.

### 3e. Phase 1 tests (extend `tests/test_chart_indicators.py`)
- width equal after add; width equalizes across 2 panes + overlay; **equalized width > the narrow oscillator's natural width** (proves padding-up actually happened, not "two zeros equal").
- exactly one visible bottom axis (price hidden, lowest pane shown); price regains bottom axis at zero panes; right axis restored to auto at zero panes.
- reorder / drag-reorder / merge / move('new') / move('price') each leave the bottom axis on the current lowest pane and widths equal.
- `set_data` re-feeds pane axes + re-equalizes; no `setWidth` recursion (call counter); own-scale `_vb2` geometry tracks the grown price ViewBox after adding a pane; studio (second `PriceChart`) parity.

---

## 4. Phase 2 — Pane hover toolbar (move up/down, maximize, delete)

### 4a. New components
- `_PaneToolbar(QWidget)` — a small floating horizontal strip of 4 `QToolButton`s styled like `_LegendRow._btn` (transparent, autoRaise, TEXT3→TEXT on hover). Buttons: `up, down, max, del`. Signals: `moveUp, moveDown, maximizeToggled, deletePane`. API: `set_can_up(bool)/set_can_down(bool)` (enable/disable directional buttons), `set_maximized(bool)` (swap max↔restore glyph+tooltip). Parented to the pane (child overlay like `_header`); hidden by default.
- `_pane_icon(kind)` — painter-drawn glyphs (`up/down/max/restore/del`) like `_eye_icon`, theme.TEXT3 — no image assets.

### 4b. Wiring
- `OscillatorPane`: add pane-level signals `paneMoveUp/paneMoveDown/paneMaximizeToggled/paneDeleteRequested = Signal(object)` (carry the pane, so a multi-indicator pane moves/deletes atomically). Keep existing per-uid `moveRequested/removeRequested` for the legend menu.
- `OscillatorPane._position_toolbar()`: `x = width() - axis_w - toolbar.width() - 4`, `move(max(0,x), 3)`, `raise_()`; `axis_w = getAxis("right").width()` (the shared width). Call on `resizeEvent`, and **also from `PriceChart._align_panes` / after `show_upto`** (axis width settles only after data) — not just the pane's own resize.
- Hover show/hide: `enterEvent` → show + reposition; `leaveEvent` → hide (child-button hover does not fire a parent leave in Qt, so no flicker); belt-and-braces 120 ms `QTimer.singleShot` re-check `cursor-in-rect` before hiding so it survives opening a menu/popup.
- `PriceChart._new_pane`: connect the 4 pane signals to `_pane_move_up/_pane_move_down/_toggle_maximize_pane/_delete_pane`; then `_refresh_pane_toolbars()`.

### 4c. Behaviors
- `_pane_move_up/down` mirror `_reorder_pane` but key off the **pane** object, clamp to index ≥ 1 (never above price at index 0), then `_after_pane_reorder()` = `_resize_panes()` + `_refresh_pane_toolbars()` + **`_align_panes()`** (Phase-1 reassign — the bottom axis must follow to the new lowest pane). *(Name reconciled to `_align_panes`, resolving the Phase 1/2 naming mismatch.)*
- `_delete_pane(pane)` = `for uid in list(pane.uids): self.remove_indicator(uid)` (last removal triggers `_unrender`'s empty-pane teardown) then `_refresh_pane_toolbars()`.
- `_toggle_maximize_pane(pane)`:
  - Maximize: `self._saved_sizes = host.sizes()`; `self._maximized_pane = pane`; give the pane the dominant share but keep a **real price floor** `max(140, total*0.15)` (not 1px) so OHLC stays visible (TV keeps price visible); `pane.set_maximized(True)`.
  - Restore: replay `self._saved_sizes` **when `len == host.count()`** (preserves user-dragged proportions, matching TV); else `_resize_panes()`. Clear `self._maximized_pane`; `pane.set_maximized(False)`.
  - `_resize_panes` **early-returns while `self._maximized_pane` is set** (so add/remove/reorder don't stomp the maximized layout).
  - Null `self._maximized_pane` in `_unrender`'s pane-drop branch and in `_delete_pane` (avoid a dangling deleted-QWidget ref).
- `_refresh_pane_toolbars()` iterates `_panes_in_visual_order()`: position p → `can_up = p>0`, `can_down = p<len-1`, `maximized = pane is self._maximized_pane`. Call from `_new_pane`, `_after_pane_reorder`, `_delete_pane`, `_unrender` pane-drop, `_toggle_maximize_pane`.
- `set_pane_host`: connect `QSplitter.splitterMoved` → clear `self._maximized_pane` (a manual drag exits maximize, like TV) + cheap `_refresh_pane_toolbars()`.
- `_maximized_pane = None` and `_saved_sizes = None` initialized in `PriceChart.__init__`.

### 4d. Phase 2 tests
- move up/down reorders splitter, topmost up is a no-op; can_up/can_down state for top/bottom panes; delete removes all indicators in a (merged) pane and drops it; maximize gives dominant share + price floor; restore preserves user-dragged sizes when count unchanged; `_resize_panes` no-op while maximized; delete maximized pane clears the lock (no dangling ref); `splitterMoved` clears the lock; toolbar clears the right axis after layout settles; **regression: full 37-test file green**; studio-instance parity.

---

## 5. Phase 3 — Settings dialog (Inputs / Style / Visibility + Defaults)

**CORE scope = no `base.py` change.** All call sites of `applied`/`_apply_edit` are internal to `chart.py` (l.543, 1492, 1518), so signatures widen freely.

### 5a. `_Indicator` style state
- Add `self.widths = [1]*max(1,len(spec.outputs))`, `self.styles = ['solid']*max(1,len(spec.outputs))` (index-aligned with `colors`/`outputs`).
- Add `@staticmethod _Indicator.spec_defaults(spec) -> (params, colors, widths, styles)` — single source of truth for the **Defaults** button and `add_indicator` seeding.

### 5b. Render plumbing
- Module-level `_pen_style(name)` → `{solid:SolidLine, dashed:DashLine, dotted:DotLine}.get(name, SolidLine)`; constants `_LINE_STYLES=[('Solid','solid'),('Dashed','dashed'),('Dotted','dotted')]`, widths `1..4`.
- `_render` overlay branch (l.1374) and `OscillatorPane._build_curves` (l.911): `pg.mkPen(color, width=ind.widths[i%len], style=_pen_style(ind.styles[i%len]))` using the same enumerate index already used for colour. `update_ind`/`_render` already rebuild curves, so edits re-pen with no extra plumbing.
- Forward-compat: read via `getattr(ind, "widths", [1])` / `getattr(ind, "styles", ["solid"])` at the pen sites (no `_Indicator` serialization exists today; this just hardens against a future session schema).

### 5c. Dialog
- Widen `applied = Signal(dict, list, list, object)` → emit `(params, colors, widths, styles, intervals)`. Update the single `.emit` (l.543) **and** the `.connect` lambda (l.1492) **and the existing tests** in the same change (see 5e).
- **Style tab:** each output row = colour button (existing) + width `QComboBox` (1..4, `userData=int`) + style `QComboBox` (Solid/Dashed/Dotted, `userData=str`). Read `currentData()` (not text). Parallel lists `_color_btns/_width_combos/_style_combos`. **For `kind=="pattern"`** (markers use brushes, not pens) hide the width/style combos.
- **Visibility tab (new, 3rd):** per-interval checkboxes built from `_TIMEFRAMES` grouped by section (exactly as `_LegendRow._open_menu` l.680–686), seeded from `ind.intervals`. On accept, contribute `intervals = None if all checked else the set`.
- **Defaults button** (footer left, before stretch): repopulate **all three tabs** from `_Indicator.spec_defaults(spec)` (+ all intervals checked) **without emitting/closing** (form-only reset, matching TV).
- Footer `Defaults ▾ / Cancel / Ok`. Bump `resize(~360, 440)`.
- **Shared interval helper:** extract the all-intervals list + "all ⇒ None" normalization into one helper used by **both** `_toggle_interval_visibility` (l.1686) and the Visibility tab, so the "all ⇒ None" rule and interval ordering have a single source of truth.

### 5d. `_apply_edit`
- Widen: `_apply_edit(self, uid, params, colors, widths=_UNSET, styles=_UNSET, intervals=_UNSET)` with a real `_UNSET` sentinel (not `or`-truthiness — avoids empty-list footguns). Existing 3-arg positional callers stay valid.
- Set `ind.params/colors`; `ind.widths/styles` when provided; `ind.intervals` when provided.
- **After writing intervals, always `self._sync_shown(ind)`** (then `_apply_visibility` + `_reveal_indicator`) in **both** branches — the existing oscillator branch (l.1501–4) never recomputes `ind.shown`, so an interval edit would otherwise not take effect until a timeframe change.
- `clone_indicator` (l.1518): forward `widths/styles/intervals` so a clone copies full style + interval visibility (TV's Clone).

### 5e. Phase 3 tests
- **Update existing** (same change): `test_settings_emits_params_on_ok` (l.234) → new arity; `test_edit_params_recomputes` (112) / `test_edit_colors_applied` (120) / clone (281) stay green via optional args.
- New: fresh `add_indicator('rsi')` has `widths==[1]*n`, `styles==['solid']*n`; width/style round-trip sets the rebuilt pen's `width`/`style` (`Qt.DashLine`) on both overlay and oscillator panes; Visibility tab covers every `_TIMEFRAMES` iv; uncheck 1m ⇒ emitted set excludes 1m and `ind.shown` flips immediately on a 1m chart (oscillator branch too); all-checked ⇒ `None`; Defaults resets every widget and emits nothing / stays open; clone copies width/style/intervals; pattern indicator hides width/style controls and applying doesn't crash. Re-run headless (`QT_QPA_PLATFORM=offscreen`).

---

## 6. Phase 4 — cross-pane crosshair & per-pane value tags (named, deferred)

Not in the approved three, but **named** so the Phase-1 crosshair regression is owned. Scope: a single full-height vertical crosshair spanning the price pane + all oscillator panes; the hovered **time** tag on the lowest pane's bottom axis; per-pane hovered-**value** tags on each pane's right scale. Until shipped, Phase 1 hides the orphaned price-chart time tag when panes exist.

---

## 7. Stretch — needs `core/indicators/base.py` (separate spec, do NOT block CORE)

- **Source dropdown** (close/open/hl2/…): the registry's `inputs` are fixed per indicator. Requires (a) a `Param`/input mechanism to mark a remappable source, and (b) the chart feeding the chosen OHLC column under that input key in `_compute`.
- **Threshold bands** (RSI 70/30, etc.): requires a per-indicator band declaration in the spec (or a generic "add hline" style) plus render support (`InfiniteLine`s on the pane) and band-fill.

---

## 8. Risk register (load-bearing)

| Risk | Mitigation |
|---|---|
| `getAxis().width()` is stale/zero → equalize fails on first add | Compute natural width from `QFontMetrics` over current tick strings; `layout.activate()` after pinning (§3a) |
| `setWidth` → resize feedback loop | `_wsyncing` re-entrancy guard (mirrors `_fitting`) |
| `_osc_panes()` dict order ≠ visual order | `_panes_in_visual_order()` helper everywhere order matters |
| Own-scale `_vb2` misaligns when price bottom axis hides | Explicit `_sync_vb2()`+`_autorange_vb2()` in `_reassign_bottom_axis` (don't rely on lazy `sigResized`) |
| Crosshair time tag orphaned by Phase 1 | Hide it when panes exist; full fix in Phase 4 |
| `_maximized_pane` dangling after delete/drag | Null it in `_unrender` pane-drop, `_delete_pane`, and on `splitterMoved` |
| `_resize_panes` stomps maximize | Early-return while `_maximized_pane` set |
| Maximize hides candles | Price floor `max(140, total*0.15)`, not 1px |
| Realign skipped on merge/move | Call `_align_panes()` at the tails of `move_indicator`, `_merge_into_adjacent`, `_render` oscillator branch |
| Settings signal-arity break | Update emit + connect lambda + the 3 existing tests in one change; sentinel-guarded optional args keep positional callers green |
| Oscillator interval edit doesn't apply | `_sync_shown(ind)` in both `_apply_edit` branches |
| Pattern indicators have no pens | Hide width/style controls for `kind=="pattern"` |
| Studio chart (2nd `PriceChart`) instance leaks | Per-instance state; cover both in tests |

## 9. Build order & shippability
1 → 2 → 3, each independently shippable and tested. Phase 2 depends on Phase 1's `_align_panes` (name reconciled). Phase 3 is independent but must update the 3 shared tests in the same change to keep the 37-green baseline that Phases 1/2 regress against.
