"""The indicator layer: a registry of self-describing technical indicators.

Each indicator is a pure function decorated with ``@indicator``, which records its metadata
(category, input series, params, outputs) in ``REGISTRY``. Consumers — the chart, API, MCP server,
strategy tester, and lab — discover and run indicators uniformly via ``list_indicators`` / ``get`` /
``compute`` / ``describe``, while the functions stay directly importable and callable.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Param:
    """A tunable indicator parameter. ``min``/``max``/``step`` drive UI controls + sweep ranges."""

    name: str
    type: str  # "int" | "float"
    default: float | int
    min: float | int | None = None
    max: float | int | None = None
    step: float | int | None = None


@dataclass(frozen=True)
class IndicatorSpec:
    """Self-description of a registered indicator."""

    name: str
    category: str
    fn: object
    inputs: list[str]
    params: list[Param]
    outputs: list[str]


REGISTRY: dict[str, IndicatorSpec] = {}


def indicator(name: str | None = None, *, category: str, inputs, params=(), outputs=None):
    """Register a pure indicator function and return it unchanged."""
    def deco(fn):
        ind_name = name or fn.__name__
        REGISTRY[ind_name] = IndicatorSpec(
            name=ind_name, category=category, fn=fn,
            inputs=list(inputs), params=list(params),
            outputs=list(outputs) if outputs else [ind_name],
        )
        return fn

    return deco


def smooth_defined(src, ma_fn, period):
    """Smooth the non-``None`` tail of ``src`` with ``ma_fn(tail, period)`` and scatter the
    results back into a full-length list aligned to ``src``.

    Positions that were ``None`` in ``src`` (warm-up / undefined) stay ``None``, as do positions
    inside ``ma_fn``'s own warm-up. Returns ``[None] * len(src)`` if fewer than ``period`` defined
    values exist. This is the shared form of the ~15 "smooth the defined tail, map back to aligned
    positions" sites across the indicator modules.
    """
    defined = [(i, v) for i, v in enumerate(src) if v is not None]
    out: list[float | None] = [None] * len(src)
    if len(defined) >= period:
        smoothed = ma_fn([v for _, v in defined], period)
        for (i, _), sv in zip(defined, smoothed, strict=True):
            out[i] = sv
    return out


def get(name: str) -> IndicatorSpec:
    """Return the spec for ``name`` (raises ``KeyError`` if unknown)."""
    if name not in REGISTRY:
        raise KeyError(f"unknown indicator: {name!r}")
    return REGISTRY[name]


def list_indicators(category: str | None = None) -> list[IndicatorSpec]:
    """All registered specs (optionally filtered by category), sorted by name."""
    specs = [s for s in REGISTRY.values() if category is None or s.category == category]
    return sorted(specs, key=lambda s: s.name)


def compute(name: str, data: dict, **params):
    """Run indicator ``name`` over a column dict ``data``; map inputs + fill param defaults."""
    spec = get(name)
    series_args = [data[key] for key in spec.inputs]
    call_params = {p.name: params.get(p.name, p.default) for p in spec.params}
    return spec.fn(*series_args, **call_params)


def describe(name: str) -> dict:
    """JSON-serializable metadata for ``name`` (for the chart / API / MCP)."""
    spec = get(name)
    return {
        "name": spec.name,
        "category": spec.category,
        "inputs": list(spec.inputs),
        "outputs": list(spec.outputs),
        "params": [
            {"name": p.name, "type": p.type, "default": p.default,
             "min": p.min, "max": p.max, "step": p.step}
            for p in spec.params
        ],
    }
