# Chart indicators — deferred features (Source dropdown, threshold bands, cross-pane crosshair)

**Status:** Approved design — ready for implementation plan
**Date:** 2026-06-04
**Branch / worktree:** `feat/chart-indicators-deferred` (stacked on `feat/chart-indicators-tv-parity` / PR #78 tip `1289e56`)
**Touches:** `src/vike_trader_app/ui/chart.py` only — **no `core/indicators/base.py` change** (verified). Tests: extend `tests/test_chart_indicators.py` (109 green baseline).

---

## 1. Goal & key finding

Deliver the three items deferred from PR #78, to TradingView/TradeLocker parity:
1. **Source dropdown** — pick the price source feeding a single-series indicator (close/open/high/low/hl2/hlc3/ohlc4/hlcc4).
2. **Threshold bands** — editable horizontal guide lines (RSI 70/30/50, etc.).
3. **Cross-pane crosshair** — one vertical line across all panes + per-pane value tags + time tag on the lowest pane.

**Verified key finding:** all three are pure **display/chart-layer** concerns. `base.compute()` already maps inputs generically (`series_args=[data[k] for k in spec.inputs]`), and `describe()` serializes only inputs/outputs/params — no registry consumer (API/MCP/tester/lab) needs source or bands. So **`base.py` is untouched**; the registry stays a clean numeric contract.

## 2. Approved decisions

- **Source (D1/D2):** offer all 8 TV sources `open/high/low/close/hl2/hlc3/ohlc4/hlcc4` (default `close`); append a non-default source to the legend label, e.g. `RSI 14 (hl2)`.
- **Bands (D3 broader / D4 editable / D5 lines-only / D6 always-union / D7 per-band colour / D8 render-all):** broader canonical set **incl.** Aroon 70/30, ADX/ADXR 25, Connors 90/10, z-score ±2/0; values **and** per-band colours editable in the Style tab; dashed lines (no fill in v1); band values are **unioned into the pane's y-range** (extend-only) so they stay on-screen; bands render for every hosted indicator in a merged pane (known mixed-scale caveat).
- **Crosshair (D9 snap / D10 value-tags / D11 pinned-legend):** vertical line **snaps to the nearest bar** across all panes; the **hovered** pane shows its horizontal segment + a value tag on its right scale; non-hovered panes show the vertical line only; the time tag is **re-homed** onto the lowest pane's bottom axis; legend row values stay **pinned to the last bar** (consistent with the existing OHLC-header decision).

## 3. Build order & the shared-signal sequence

**Order A → B → C.**
- **Phase A — Crosshair (first, independent):** does NOT touch `applied`/`_apply_edit`/`clone`, so it can't collide with B/C.
- **Phase B — Source dropdown (second):** performs the first `applied`/`_apply_edit`/lambda/clone/test arity bump (adds `source: str`).
- **Phase C — Threshold bands (third):** performs the second arity bump (adds `bands: list`), inheriting B's widened plumbing.

**Signal-arity sequence (the single biggest shared risk).** `_IndicatorSettings.applied` is currently `QtCore.Signal(dict, list, list, list, object)` (params, colors, widths, styles, intervals). It widens in two deliberate steps:
- After B: `Signal(dict, list, list, list, object, str)` (+`source`).
- After C: `Signal(dict, list, list, list, object, str, list)` (+`bands`).

Each step updates **in lockstep**: `applied` declaration → `_accept` emit → `edit_indicator` connect lambda → `_apply_edit` signature → `clone_indicator` call → **`tests/test_chart_indicators.py::test_settings_emits_params_on_ok` lambda**. New `_apply_edit` params use `=_UNSET` (mirrors widths/styles/intervals) so other callers stay valid mid-sequence.

---

## 4. Phase A — Cross-pane crosshair

**Pre-step:** extract the inline `_tag_qss` style string (in `PriceChart.__init__`) to a module-level constant so `OscillatorPane` tags match the price-pane tags.

**`OscillatorPane`:**
- `__init__`: add `self._cx_v = pg.InfiniteLine(angle=90, movable=False, pen=cx_pen)`, `addItem(..., ignoreBounds=True)` (mandatory — else the line forces the pane x-range), `hide()`. Add `self._cx_val_tag` and `self._cx_time_tag` `QLabel`s (shared tag QSS, `WA_TransparentForMouseEvents`, hidden). Connect `self.scene().sigMouseMoved` → `_on_pane_mouse_moved`.
- `set_crosshair_x(x)`: position+show `_cx_v` at `round(x)` (snap); compute the hosted indicator's value at that bar index and place `_cx_val_tag` at the right edge (`width - axis_w - tag.width() - 1`) at the value's scene-y. Guard: skip if `round(x)` unchanged (FullViewportUpdate repaints the whole pane per move).
- `clear_crosshair()`: hide `_cx_v` / `_cx_val_tag` / `_cx_time_tag`.
- `set_time_tag(text, scene_x)`: bottom-edge time label at `y = height - h - 1`, `x` from **this pane's** `mapViewToScene` (not the price scene x).
- `_on_pane_mouse_moved(scene_pos)`: map via `getViewBox()`; if outside `sceneBoundingRect` emit `crosshairLeft`, else emit `crosshairMoved(x)`; on the in-pane case also draw this pane's horizontal segment + value at the real hovered y.
- New signals `crosshairMoved = Signal(float)`, `crosshairLeft = Signal()`. Extend `leaveEvent` to also emit `crosshairLeft` (keep the existing toolbar hide/timer logic intact).

**`PriceChart`:**
- `_set_crosshair_x(x)`: set price `_cx_v` at `round(x)`; `for p in self._panes_in_visual_order(): p.set_crosshair_x(x)`; compute the time string from `x_to_ts(self._bars, x)` and call `panes[-1].set_time_tag(...)` when panes exist, else the existing price-chart bottom-axis path.
- `_clear_crosshair()`: hide price `_cx_v/_cx_h/_cx_price_tag/_cx_time_tag`, call `clear_crosshair()` on every pane, `_show_last_ohlc()`.
- `_on_mouse_moved`: IN branch → `_set_crosshair_x(pt.x())` (plus keep local `_cx_h` + `_cx_price_tag`); OUT branch → `_clear_crosshair()`; **replace** the Phase-1 "hide time tag when panes exist" block with the re-home call.
- New `leaveEvent` → `_clear_crosshair()`.
- `_new_pane`: connect `pane.crosshairMoved → _set_crosshair_x` and `pane.crosshairLeft → _clear_crosshair` (alongside existing wiring).

**Risks:** per-widget scenes — never pass raw `scene_pos` across widgets; convert to bar-index x via `mapSceneToView`, re-map per pane via `mapViewToScene`. `ignoreBounds=True` mandatory. Throttle on unchanged `round(x)`. Don't clash with the replay `_cursor` (panes have none today). Clearing on leave must cover the splitter-gutter case (both `leaveEvent` and out-of-rect branch clear).

**Test deltas:** `test_crosshair_time_tag_hidden_when_panes_exist` **inverts** — assert price `_cx_time_tag` hidden AND lowest pane's time tag shown. `test_crosshair_time_tag_shown_with_no_panes` unchanged. New: hover-price-fans-to-panes (each pane `_cx_v` at the same bar-x), hover-pane-fans-to-price (`_cx_h` hidden), leave clears all, out-of-rect clears, value-tag at known x. **Offscreen:** assert QLabel tags via `not isHidden()` (isVisible() is always False offscreen); `_cx_v.isVisible()` works (InfiniteLine on a shown split).

---

## 5. Phase B — Source dropdown

- Module helpers: `is_source_selectable(spec) -> len(spec.inputs)==1 and spec.inputs[0] in {'close','open','high','low'}` (53 indicators, all `close`); `_SOURCE_OPTIONS = ['open','high','low','close','hl2','hlc3','ohlc4','hlcc4']`; `_source_series(data, source)` returns the raw column for o/h/l/c, else derives `hl2=(h+l)/2`, `hlc3=(h+l+c)/3`, `ohlc4=(o+h+l+c)/4`, `hlcc4=(h+l+2c)/4` (pure, list-based, matching `_data_cols`).
- `_Indicator.__init__`: `self.source = "close"`. `_Indicator.label`: append `(source)` when `source != "close"`. `spec_defaults`: include source default `"close"`.
- `PriceChart._compute`: after `data = self._data_cols()`, before `_base.compute`, if `is_source_selectable(ind.spec)` and `getattr(ind,'source','close') != 'close'`: `data[ind.spec.inputs[0]] = _source_series(data, ind.source)`. (Single load-bearing remap; close path is zero-overhead.)
- `_IndicatorSettings.__init__`: when `is_source_selectable(self._spec)`, insert a **Source** `QComboBox` as the FIRST Inputs-tab row (TV places it above numeric inputs); `userData` = source key; current = `getattr(ind,'source','close')`; store `self._source_combo` (None otherwise). `_accept` reads it; `_reset_defaults` resets to `close`.
- Thread: widen `applied` to `(…, object, str)`; `edit_indicator` lambda + `_apply_edit(..., source=_UNSET)` (assign `ind.source` BEFORE `_compute`); `clone_indicator` carries `source`.

**Risks:** gate strictly on `is_source_selectable` (pairs `['close','benchmark']` and `volume_osc` `['volume']` excluded automatically). Multi-output (macd/bollinger/…) still work — only the input column is swapped. Default `close` preserves existing behavior/tests.

**Test deltas:** `test_settings_emits_params_on_ok` lambda gains a `source` arg. New: `is_source_selectable` count == 53; `_source_series` math; hl2-SMA behavioural (differs from close-fed, matches hand-computed); default-source byte-identical regression; multi-output (bollinger on ohlc4) all bands populated; UI shows combo for rsi/sma, hides for stochastic/obv/volume_osc/pattern; clone preserves non-default source.

---

## 6. Phase C — Threshold bands

- Module table `_INDICATOR_BANDS: dict[str, list[tuple[str, float]]]` (near `_OVERLAY_NAMES`), e.g. `rsi: [('Upper',70),('Middle',50),('Lower',30)]`, `stochastic/stochf/stochrsi: 80/20`, `williams_r: -20/-80`, `cci: +100/0/-100`, `ultosc: 70/30`, **broader:** `aroon: 70/30`, `adx/adxr: 25`, `connors_rsi: 90/10`, `zscore/spread_zscore: +2/0/-2`, plus a **0-centerline family** (macd/ppo/apo/mom/roc/rocp/ao/ac/dpo/trix/tsi/smi_ergodic/cmo/elder_ray/kvo/adosc/net_volume/bop). **Verify each value against the fn's native output range** (e.g. williams_r is [-100,0] → -20/-80, NOT 20/80). **Do not add `mfi`** (not registered).
- `_Indicator.__init__`: `self.bands = [list(b) for b in _INDICATOR_BANDS.get(name, [])]` (mutable per-instance copies) + per-band colours (default dim `theme.TEXT3`). Only oscillator/pairs kinds get bands. `spec_defaults`/a `band_defaults(name)` returns the canonical seed for the Defaults button.
- `OscillatorPane`: build band lines after curves — `pg.InfiniteLine(angle=0, pos=value, movable=False, pen=dashed dim/per-band colour)` stored in `self._band_lines[uid]` (**kept OUT of `self._curves`** so they don't pollute `reveal`'s `all_ys` / `set_value` / the crosshair value-at-x scan). Remove/rebuild in `remove_ind`/`update_ind` the same way curves are. `reveal`: **union** band values into `lo/hi` (extend-only, never override) so they stay visible.
- `_IndicatorSettings` Style tab: after the per-output rows, add per-band rows when `ind.bands` non-empty — a `QDoubleSpinBox` value + a colour button per band, labelled `<Indicator> <BandLabel>` (e.g. "RSI Upper Band"). Collect `self._band_value_spins` / `self._band_color_btns`. `_reset_defaults` repopulates from the canonical seed.
- Thread: widen `applied` to `(…, str, list)` (bands payload `[(label, value, color), …]`); `_accept` reads spins/colours; `edit_indicator` lambda + `_apply_edit(..., bands=_UNSET)` (assign `ind.bands`; for oscillator/pairs `update_ind` rebuilds band lines); `clone_indicator` carries `bands`.

**Risks:** signal arity (sequenced after B). Band lines out of `_curves`. Sign/scale per indicator (verify). Merged panes render all hosted bands (caveat). `update_ind` must remove+rebuild band lines (no orphans/dupes). Crosshair value tag reads series only, never bands.

**Test deltas:** `test_settings_emits_params_on_ok` lambda gains a `bands` arg. New: band seeding (rsi → 70/50/30; macd → single 0; ema overlay → []); render as N `InfiniteLine(angle=0)` not in `_curves`; `reveal` y-range contains every band value with series still in range; Style-tab band edit round-trip (70→80 updates `ind.bands` + the rendered line); Defaults reset; `remove_ind` removes band lines; clone carries edited bands. Guard `test_oscillator_reveals_in_lockstep` still passes with band union.

---

## 7. Cross-feature integration risks

| Risk | Mitigation |
|---|---|
| Shared `applied`/`_apply_edit`/lambda/clone/test (B+C) | Sequence B then C; `_UNSET` defaults; one test-lambda edit per phase |
| Band lines vs autoscale / value lookups (A+C) | Band `InfiniteLine`s stored in `_band_lines`, never `_curves`; `reveal` unions extend-only; crosshair value tag reads series only |
| Source on multi-output (B) | Only the input column is swapped; outputs/_clean/_render unchanged; gate strictly on `is_source_selectable` |
| Source + bands on same instance (B+C) | Both on `_Indicator`, both threaded through `_apply_edit`; `clone_indicator` must carry **both** |
| Crosshair perf with N panes (A) | `ignoreBounds=True`; skip `set_crosshair_x` when `round(x)` unchanged |
| Per-widget scenes (A) | Convert to bar-index x (`mapSceneToView`) and re-map per pane (`mapViewToScene`); time-tag x from the lowest pane's own mapping |

## 8. Out of scope (v1)
Band **fill** (between upper/lower) deferred. Updating legend row values to the hovered bar deferred (legend stays pinned-to-last). `source`/`bands` are NOT added to the registry `describe()`/API/MCP surface (chart-only).
