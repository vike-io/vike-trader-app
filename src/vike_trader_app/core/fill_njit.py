"""Shared ``@njit`` cost-core — numba-compiled ports of ``fill.compute_fill`` and
``broker_sim`` primitives.

These functions are the deduplication foundation: both the existing single-asset kernel
(``fastsim._sim_kernel``) and the future multi-asset kernel will call into this module
rather than inlining their own copies. Until a later perf-gated stage wires them in,
nothing except this module's parity test imports them.

``compute_fill_nb`` returns a fixed-size 8-tuple because numba cannot return a dataclass:

    (kind_int, new_size, new_avg_px, closing_qty, entry_avg_px, realized_pnl, portion, leftover)

``kind_int`` encoding (matches ``fill.FillOutcome.kind`` strings 1-to-1):

    KIND_OPEN   = 0  — "open"   flat -> new position
    KIND_ADD    = 1  — "add"    same-direction top-up
    KIND_REDUCE = 2  — "reduce" partial close (remainder survives)
    KIND_CLOSE  = 3  — "close"  full close -> flat
    KIND_FLIP   = 4  — "flip"   close-and-reverse through zero

Python reverse mapping: ``FILL_KIND_NAMES = {0:"open", 1:"add", 2:"reduce", 3:"close", 4:"flip"}``
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# kind_int constants — importable from Python side for the parity test.        #
# --------------------------------------------------------------------------- #
KIND_OPEN   = 0
KIND_ADD    = 1
KIND_REDUCE = 2
KIND_CLOSE  = 3
KIND_FLIP   = 4

FILL_KIND_NAMES: dict[int, str] = {
    KIND_OPEN:   "open",
    KIND_ADD:    "add",
    KIND_REDUCE: "reduce",
    KIND_CLOSE:  "close",
    KIND_FLIP:   "flip",
}
FILL_KIND_INTS: dict[str, int] = {v: k for k, v in FILL_KIND_NAMES.items()}

_EPS = 1e-12

# --------------------------------------------------------------------------- #
# njit shim (mirrors fastsim.py exactly)                                       #
# --------------------------------------------------------------------------- #

def _noop_njit(*args, **kwargs):
    """Fallback for ``numba.njit`` when the optional ``[fast]`` extra is absent.

    Supports both the bare ``@_noop_njit`` form and the parametrised
    ``@_noop_njit(cache=True)`` form.
    """
    if args and callable(args[0]):
        return args[0]

    def _decorator(fn):
        return fn

    return _decorator


try:
    from numba import njit  # type: ignore[import]
    _HAS_NUMBA = True
except ImportError:  # pragma: no cover
    _HAS_NUMBA = False
    njit = _noop_njit  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Scalar cost helpers — 1:1 ports of broker_sim.py                            #
# --------------------------------------------------------------------------- #

@njit(inline='always', cache=True)
def adverse_fill_price_nb(raw: float, side: int, slippage: float) -> float:
    """Fill price after adverse slippage (buys up, sells down). Port of ``broker_sim.adverse_fill_price``."""
    return raw * (1.0 + side * slippage)


@njit(inline='always', cache=True)
def fee_nb(size: float, price: float, rate: float, multiplier: float) -> float:
    """Transaction fee on the multiplier-scaled notional. Port of ``broker_sim.fee``."""
    return size * price * rate * multiplier


@njit(inline='always', cache=True)
def funding_charge_nb(pos: float, mark: float, rate: float, multiplier: float) -> float:
    """Perp funding cash flow. Port of ``broker_sim.funding_charge``."""
    return pos * mark * rate * multiplier


# --------------------------------------------------------------------------- #
# compute_fill_nb — 1:1 port of fill.compute_fill                             #
# --------------------------------------------------------------------------- #

@njit(inline='always', cache=True)
def compute_fill_nb(
    prior_size: float,
    prior_avg: float,
    side: int,
    qty: float,
    price: float,
    multiplier: float,
) -> tuple:
    """Position-transition arithmetic: numba port of ``fill.compute_fill``.

    Returns ``(kind_int, new_size, new_avg_px, closing_qty, entry_avg_px,
               realized_pnl, portion, leftover)`` — the same 8 fields as
    ``FillOutcome``, with ``kind`` encoded as an int (see module docstring).

    Branch logic is byte-identical to ``fill.compute_fill``; the only diff is
    returning a tuple instead of a dataclass and using ``kind_int`` constants.
    """
    delta = side * qty

    # ---- open from flat --------------------------------------------------
    if prior_size == 0.0:
        # kind=KIND_OPEN, new_size=delta, new_avg=price, rest zeros
        return (KIND_OPEN, delta, price, 0.0, 0.0, 0.0, 0.0, 0.0)

    # ---- add in the same direction ---------------------------------------
    if (prior_size > 0.0) == (delta > 0.0):
        new_size = prior_size + delta
        new_avg = (prior_avg * abs(prior_size) + price * abs(delta)) / abs(new_size)
        return (KIND_ADD, new_size, new_avg, 0.0, 0.0, 0.0, 0.0, 0.0)

    # ---- opposite direction: reduce / close / flip -----------------------
    sign = 1.0 if prior_size > 0.0 else -1.0
    closing = min(abs(delta), abs(prior_size))
    portion = closing / abs(prior_size)
    realized = (price - prior_avg) * (sign * closing) * multiplier
    remaining = abs(prior_size) - closing

    if remaining > _EPS:
        # partial reduce — remainder keeps cost basis
        return (KIND_REDUCE, sign * remaining, prior_avg, closing, prior_avg,
                realized, portion, 0.0)

    leftover = abs(delta) - closing
    if leftover > _EPS:
        # crossed zero — open opposite at fill price
        new_size = (1.0 if delta > 0.0 else -1.0) * leftover
        return (KIND_FLIP, new_size, price, closing, prior_avg,
                realized, portion, leftover)

    # fully closed -> flat
    return (KIND_CLOSE, 0.0, 0.0, closing, prior_avg, realized, portion, 0.0)
