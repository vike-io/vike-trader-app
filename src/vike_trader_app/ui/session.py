"""Session persistence — a Qt-free seam for the shell's save/restore-on-launch.

The shell saves a small JSON snapshot on close (window geometry, active space, symbol +
interval, panel toggles, and each chart's user indicators) and restores it on the next
launch, so the app reopens where the user left off (AmiBroker/MultiCharts table stakes).

Qt-free by design so it unit-tests without a QApplication: geometry travels as an opaque
hex string (``QWidget.saveGeometry().toHex()``), and indicator (de)hydration is duck-typed
against ``PriceChart`` — it only touches ``_indicators`` / ``add_indicator`` / ``_apply_edit``
and friends, so tests drive it with a fake. Persistence must never break the app: a missing,
corrupt, or stale file loads as ``None`` (fresh-start defaults), and save errors are swallowed
(a failed write on shutdown is not worth a crash dialog).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

_VERSION = 1

# Pairs indicators need a 2nd symbol's closes aligned to the loaded bars — that benchmark
# isn't ours to persist (it's re-fetched per load), so they're dropped across sessions,
# matching the in-app reload behaviour (_recompute_indicators drops pairs too).
_SKIP_KINDS = {"pairs"}


@dataclass
class SessionState:
    """Everything restored on launch. Defaults mirror a fresh first run."""

    symbol: str = "BTCUSDT"
    interval: str = "1m"
    space: int = 0                 # rail/tab index of the active space
    geometry_hex: str = ""         # QWidget.saveGeometry() as hex ("" = none saved)
    maximized: bool = True
    panels: dict = field(default_factory=dict)              # panel key -> shown
    chart_indicators: list = field(default_factory=list)    # Chart space (price)
    studio_indicators: list = field(default_factory=list)   # Studio chart (studio_price)

    def to_dict(self) -> dict:
        return {"version": _VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, raw) -> "SessionState | None":
        """Tolerant parse: unknown keys ignored, wrong-typed values fall back to defaults."""
        if not isinstance(raw, dict):
            return None
        state = cls()
        for f in fields(cls):
            if f.name not in raw:
                continue
            value = raw[f.name]
            if isinstance(value, type(getattr(state, f.name))):
                setattr(state, f.name, value)
        return state


def load_session(path) -> SessionState | None:
    """Read a saved session; ``None`` (fresh start) on any missing/corrupt/unreadable file."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no file / bad JSON / locked -> fresh start
        return None
    return SessionState.from_dict(raw)


def save_session(path, state: SessionState) -> bool:
    """Atomically write ``state`` (tmp file + replace, so a crash mid-write can't corrupt
    the previous session). Returns False instead of raising — shutdown must not fail."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")  # same dir -> same filesystem -> atomic replace
        tmp.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception:  # noqa: BLE001 - read-only dir / disk full -> skip persistence
        return False


# --- per-chart indicator (de)hydration ------------------------------------------------------


def indicator_states(chart) -> list[dict]:
    """Serialize a chart's user indicators (params + style + smoothing + bands) to plain
    dicts. Pairs are skipped (see _SKIP_KINDS)."""
    out = []
    for ind in getattr(chart, "_indicators", {}).values():
        if ind.kind in _SKIP_KINDS:
            continue
        out.append({
            "name": ind.name,
            "params": dict(ind.params),
            "colors": list(ind.colors),
            "widths": list(getattr(ind, "widths", [])),
            "styles": list(getattr(ind, "styles", [])),
            "intervals": sorted(ind.intervals) if ind.intervals is not None else None,
            "source": getattr(ind, "source", "close"),
            "smooth_type": getattr(ind, "smooth_type", None),
            "smooth_len": int(getattr(ind, "smooth_len", 14)),
            "smooth_color": getattr(ind, "smooth_color", None),
            "bands": [[lbl, float(val)] for lbl, val in getattr(ind, "bands", [])],
            "band_colors": list(getattr(ind, "band_colors", [])),
            "visible": bool(getattr(ind, "visible", True)),
        })
    return out


def apply_indicator_states(chart, states) -> int:
    """Re-add saved indicators onto ``chart`` (which must already have bars loaded).

    Mirrors ``clone_indicator``'s flow: ``add_indicator(name, params)`` then ``_apply_edit``
    for style/intervals/source/bands, plus the smoothing fields set directly. One bad saved
    entry is skipped, never fatal. Returns the number successfully applied.
    """
    applied = 0
    for st in states or []:
        try:
            ind = chart.add_indicator(st["name"], params=dict(st.get("params") or {}))
            if ind is None:  # unknown indicator / no bars / pairs awaiting a benchmark
                continue
            if st.get("smooth_type"):
                ind.smooth_type = st["smooth_type"]
                ind.smooth_len = int(st.get("smooth_len", 14))
                if st.get("smooth_color"):
                    ind.smooth_color = st["smooth_color"]
            # bands payload is (label, value, color) triples; colours missing from the saved
            # state fall back to the freshly-seeded defaults on the new instance.
            seeded = list(getattr(ind, "band_colors", []))
            saved_colors = st.get("band_colors") or []
            payload = []
            for i, (lbl, val) in enumerate(st.get("bands") or []):
                color = (saved_colors[i] if i < len(saved_colors)
                         else seeded[i] if i < len(seeded) else "#888888")
                payload.append((lbl, float(val), color))
            intervals = st.get("intervals")
            chart._apply_edit(
                ind.uid, dict(ind.params), list(st.get("colors") or ind.colors),
                widths=list(st.get("widths") or ind.widths),
                styles=list(st.get("styles") or ind.styles),
                intervals=set(intervals) if intervals is not None else None,
                source=st.get("source", "close"),
                bands=payload,
            )
            if not st.get("visible", True):
                ind.visible = False
                chart._sync_shown(ind)
                chart._apply_visibility(ind)
            applied += 1
        except Exception:  # noqa: BLE001 - one stale/bad entry must not break the rest
            continue
    return applied
