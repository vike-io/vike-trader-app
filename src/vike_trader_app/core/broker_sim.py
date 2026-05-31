"""broker_sim — the canonical cost model. ONE definition of fills, fees, and funding.

Both the event engine (``engine.py``) and the compiled kernel (``fastsim.py``) must use
*these* formulas. The engine imports them directly; the kernel mirrors them inline (numba
cannot call back into Python cheaply) and is pinned to them by the engine↔kernel parity
tests (``tests/test_fastsim.py`` today; a dedicated ``tests/test_reconciliation.py`` is
added later in Phase 1). Change a cost rule HERE and update the kernel's mirror in the
same commit, or the parity tests fail.

Convention: ``side_sign`` is +1 for a buy, -1 for a sell. ``multiplier`` scales every
notional term (contract size). Slippage is adverse: buys fill up, sells fill down.
"""


def adverse_fill_price(raw_price: float, side_sign: int, slippage: float) -> float:
    """The fill price after adverse slippage: buys (+1) up, sells (-1) down."""
    return raw_price * (1.0 + side_sign * slippage)


def fee(size: float, price: float, rate: float, multiplier: float) -> float:
    """Transaction fee = ``rate`` on the (multiplier-scaled) notional (>= 0 for non-negative size/rate)."""
    return size * price * rate * multiplier


def funding_charge(
    pos_size: float, mark_price: float, funding_rate: float, multiplier: float
) -> float:
    """Perp funding cash flow for a held position: longs pay positive funding, shorts receive."""
    return pos_size * mark_price * funding_rate * multiplier
