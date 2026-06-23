"""Format order qty/price as DECIMAL STRINGS quantized to the symbol's stepSize/tickSize.

RiskGate rounds to tick/lot but round() leaves IEEE artifacts (0.30000000000000004) that trigger
Binance -1111 BAD_PRECISION. We quantize DOWN (ROUND_DOWN, never overshoot a limit) to the step's
decimal places and emit a plain (non-exponent) string.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def format_to_step(value: float, step: str | float) -> str:
    """Quantize `value` down to `step`'s precision; return a plain decimal string.

    When `step` is 0 or missing (e.g. LOT_SIZE/PRICE_FILTER absent from exchangeInfo), dividing by
    zero would raise; instead return the value as-is (no rounding) so submit() can still attempt
    the order rather than crashing.
    """
    step_d = Decimal(str(step))
    if step_d <= 0:
        return format(Decimal(str(value)), "f")
    quantized = (Decimal(str(value)) / step_d).to_integral_value(rounding=ROUND_DOWN) * step_d
    # Normalize to step's exponent so trailing zeros match the venue's expected precision.
    exponent = step_d.normalize().as_tuple().exponent
    if exponent < 0:
        quantized = quantized.quantize(step_d)
    return format(quantized, "f")


def format_qty(qty: float, step: str | float) -> str:
    return format_to_step(qty, step)


def format_price(price: float, tick: str | float) -> str:
    return format_to_step(price, tick)
